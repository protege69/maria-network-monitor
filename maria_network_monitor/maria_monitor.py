#!/usr/bin/env python3

import asyncio
import os
import ipaddress
import json
import re
import signal
import socket
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from dashboard import (NewPrinterDiscovery, discovery_configs, export_assets,
                       export_dashboard, site_key, update_inventory)

from reports import update_reports, site_report, report_discovery
from resilience import confirm_absences, RouterPoller, BoundedPublisher

import paho.mqtt.client as mqtt
import routeros_api
from pyipp import IPP


OPTIONS_FILE = Path("/data/options.json")
KNOWN_FILE = Path("/data/known.json")
PRINTER_CACHE_FILE = Path("/data/printer_cache.json")
INVENTORY_FILE = Path("/data/dashboard_inventory.json")

DISCOVERY_PREFIX = "homeassistant"
MQTT_ROOT = "maria-monitor"
AVAILABILITY_TOPIC = f"{MQTT_ROOT}/availability"

REPORT_FILE = Path("/data/daily_reports.json")
ROUTER_POLLER = None
STOP = threading.Event()

PRINTER_CACHE = {}


PORTS = {
    80: "http",
    443: "https",
    445: "smb",
    515: "lpd",
    554: "rtsp",
    631: "ipp",
    5060: "sip",
    5061: "sips",
    8000: "camera_8000",
    8080: "http_8080",
    8291: "winbox",
    9100: "jetdirect",
}


def log(message):
    print(
        f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}",
        flush=True,
    )


def load_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def save_json(path, data):
    tmp = path.with_suffix(".tmp")

    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2,
        )

    tmp.replace(path)



def restore_printer_cache():
    saved = load_json(
        PRINTER_CACHE_FILE,
        {},
    ) or {}

    restored = 0

    for device_id, item in saved.items():
        if not isinstance(item, dict):
            continue

        PRINTER_CACHE[device_id] = {
            "ipp": item.get("ipp"),
            "snmp": item.get("snmp"),
            "ipp_observed_at": item.get("ipp_observed_at"),
            "snmp_observed_at": item.get("snmp_observed_at"),
            "ipp_fresh": False,
            "snmp_fresh": False,

            # monotonic нельзя сохранять между
            # перезапусками. После старта сразу
            # попробуем опросить принтер заново.
            "ipp_at": 0.0,
            "snmp_at": 0.0,
        }

        restored += 1

    log(
        f"Printer cache restored: "
        f"{restored} devices"
    )


def save_printer_cache():
    data = {}

    for device_id, item in PRINTER_CACHE.items():
        data[device_id] = {
            "ipp": item.get("ipp"),
            "snmp": item.get("snmp"),
            "ipp_observed_at": item.get("ipp_observed_at"),
            "snmp_observed_at": item.get("snmp_observed_at"),
        }

    save_json(
        PRINTER_CACHE_FILE,
        data,
    )



def is_true(value):
    return str(value).lower() in (
        "true",
        "yes",
        "1",
    )


def clean_mac(mac):
    raw = re.sub(
        r"[^0-9A-Fa-f]",
        "",
        mac or "",
    )

    if len(raw) != 12:
        return None

    raw = raw.upper()

    return ":".join(
        raw[i:i + 2]
        for i in range(0, 12, 2)
    )


def device_id(mac):
    return (
        "maria_"
        + mac.lower().replace(":", "")
    )


def safe_name(name):
    """
    Не даём случайно опубликовать пароль,
    записанный в DHCP comment.
    """

    name = (name or "").strip()

    if not name:
        return "Network device"

    name = re.split(
        r"\b(?:password|passwd|pass|pwd|пароль)\b",
        name,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0].strip(" :-_")

    return name or "Network device"


def site_slug(name):
    return re.sub(
        r"[^a-z0-9]+",
        "_",
        name.lower(),
    ).strip("_")


def detect_lan(addresses):
    candidates = []

    for row in addresses:

        try:
            iface = ipaddress.ip_interface(
                row.get("address", "")
            )
        except ValueError:
            continue

        if not iface.ip.is_private:
            continue

        if iface.network.prefixlen != 24:
            continue

        interface_name = (
            row.get("interface") or ""
        ).lower()

        score = (
            100
            if "bridge" in interface_name
            else 0
        )

        candidates.append(
            (
                score,
                str(iface.network),
            )
        )

    if not candidates:
        return None

    candidates.sort(reverse=True)

    return candidates[0][1]


def ping_host(ip):
    try:
        result = subprocess.run(
            [
                "ping",
                "-c",
                "1",
                "-W",
                "1",
                ip,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2,
        )

        return result.returncode == 0

    except Exception:
        return False


def probe_ports(ip):
    opened = {}

    for port, name in PORTS.items():

        try:
            with socket.create_connection(
                (ip, port),
                timeout=0.30,
            ):
                opened[name] = port

        except Exception:
            pass

    return opened


def probe_device(ip):
    return {
        "ping": ping_host(ip),
        "ports": probe_ports(ip),
    }


async def probe_ipp_async(ip):
    paths = (
        "/ipp/print",
        "/ipp",
        "/",
    )

    last_error = None

    for base_path in paths:
        ipp = IPP(
            ip,
            base_path=base_path,
            port=631,
            request_timeout=5,
            ipp_version=(1, 1),
        )

        try:
            printer = await ipp.printer()

            markers = []

            for item in printer.markers:
                markers.append(
                    {
                        "type": item.marker_type,
                        "name": item.name,
                        "color": item.color,
                        "level": item.level,
                        "low_level": item.low_level,
                        "high_level": item.high_level,
                    }
                )

            return {
                "ok": True,
                "base_path": base_path,

                "manufacturer": (
                    printer.info.manufacturer
                ),

                "model": (
                    printer.info.model
                ),

                "printer_name": (
                    printer.info.printer_name
                ),

                "uuid": (
                    printer.info.uuid
                ),

                "serial": (
                    printer.info.serial
                ),

                "uptime": (
                    printer.info.uptime
                ),

                "pages_per_minute": (
                    printer.info.pages_per_minute
                ),

                "pages_per_minute_color": (
                    printer.info.pages_per_minute_color
                ),

                "state": (
                    printer.state.printer_state
                ),

                "reasons": normalize_reasons(printer.state.reasons),

                "accepting_jobs": (
                    printer.status.accepting_jobs
                ),

                "queued_jobs": (
                    printer.status.queued_jobs
                ),

                "markers": markers,

                "counters": {
                    "impressions_completed": (
                        printer.counters.impressions_completed
                    ),
                    "pages_completed": (
                        printer.counters.pages_completed
                    ),
                    "media_sheets_completed": (
                        printer.counters.media_sheets_completed
                    ),
                },
            }

        except Exception as exc:
            last_error = str(exc)

        finally:
            try:
                await ipp.close()
            except Exception:
                pass

    return {
        "ok": False,
        "error": last_error,
    }


def probe_ipp(ip):
    try:
        return asyncio.run(
            probe_ipp_async(ip)
        )

    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
        }


def snmp_get_values(ip, community, oid):
    try:
        result = subprocess.run(
            [
                "snmpwalk",
                "-v2c",
                "-c",
                community,
                "-t",
                "1",
                "-r",
                "0",
                "-Oqv",
                ip,
                oid,
            ],
            capture_output=True,
            text=True,
            timeout=3,
        )

        if result.returncode != 0:
            return []

        return [
            line.strip()
            for line in result.stdout.splitlines()
            if line.strip()
        ]

    except Exception:
        return []



def decode_snmp_text(value):
    value = (value or "").strip()

    if value.startswith('"') and value.endswith('"'):
        value = value[1:-1].strip()

    if re.fullmatch(
        r"(?:[0-9A-Fa-f]{2}\s+)*[0-9A-Fa-f]{2}",
        value,
    ):
        try:
            return bytes.fromhex(value).decode(
                "utf-8",
                errors="replace",
            ).strip()
        except Exception:
            pass

    return value


def snmp_get_text_values(ip, community, oid):
    try:
        result = subprocess.run(
            [
                "snmpwalk",
                "-v2c",
                "-c",
                community,
                "-t",
                "1",
                "-r",
                "0",
                "-Oqv",
                ip,
                oid,
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )

        if result.returncode != 0:
            return []

        values = []
        current = []

        for line in result.stdout.splitlines():
            line = line.strip()

            if not line:
                continue

            if current:
                current.append(line)

                if line.endswith('"'):
                    values.append(
                        decode_snmp_text(
                            " ".join(current)
                        )
                    )
                    current = []

                continue

            if line.startswith('"') and not line.endswith('"'):
                current = [line]
                continue

            values.append(
                decode_snmp_text(line)
            )

        if current:
            values.append(
                decode_snmp_text(
                    " ".join(current)
                )
            )

        return values

    except Exception:
        return []


def short_supply_name(name):
    name = (name or "").strip()

    if not name:
        return "Consumable"

    name = re.split(
        r",\s*PN\b|;\s*SN",
        name,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0].strip()

    return name or "Consumable"


def guess_supply_type(name):
    text = (name or "").lower()

    if "waste" in text or "отработ" in text:
        return "waste-toner"

    if (
        "opc" in text
        or "drum" in text
        or "принт-картридж" in text
    ):
        return "opc"

    if "toner" in text or "тонер" in text:
        return "toner"

    return "supply"


def probe_snmp_printer(ip, community="public"):
    counter_oid = "1.3.6.1.2.1.43.10.2.1.4"
    serial_oid = "1.3.6.1.2.1.43.5.1.1.17"

    descriptions = snmp_get_text_values(
        ip,
        community,
        "1.3.6.1.2.1.43.11.1.1.6",
    )

    max_values = snmp_get_values(
        ip,
        community,
        "1.3.6.1.2.1.43.11.1.1.8",
    )

    level_values = snmp_get_values(
        ip,
        community,
        "1.3.6.1.2.1.43.11.1.1.9",
    )

    counter_values = snmp_get_values(
        ip,
        community,
        counter_oid,
    )

    serial_values = snmp_get_values(
        ip,
        community,
        serial_oid,
    )

    page_count = None

    for value in counter_values:
        match = re.search(r"-?\d+", value)

        if match:
            number = int(match.group())

            if number >= 0:
                page_count = number
                break

    serial = None

    if serial_values:
        serial = serial_values[0].strip().strip('"') or None

    supplies = []

    count = min(
        len(descriptions),
        len(max_values),
        len(level_values),
    )

    for index in range(count):
        try:
            maximum = int(
                re.search(
                    r"-?\d+",
                    max_values[index],
                ).group()
            )

            level = int(
                re.search(
                    r"-?\d+",
                    level_values[index],
                ).group()
            )

        except Exception:
            continue

        percent = None

        if maximum > 0 and level >= 0:
            percent = round(
                level * 100 / maximum
            )

            percent = max(
                0,
                min(100, percent),
            )

        name = short_supply_name(
            descriptions[index]
        )

        supplies.append(
            {
                "index": index,
                "type": guess_supply_type(name),
                "name": name,
                "color": None,
                "level": level,
                "low_level": None,
                "high_level": maximum,
                "percent": percent,
            }
        )

    return {
        "ok": bool(
            page_count is not None
            or serial
            or supplies
        ),
        "page_count": page_count,
        "serial": serial,
        "supplies": supplies,
    }


def normalize_printer_data(ipp, snmp):
    ipp = ipp or {}
    snmp = snmp or {}

    page_count = snmp.get("page_count")
    page_count_source = "snmp" if page_count is not None else None

    if page_count is None:
        counters = ipp.get("counters") or {}

        for key in (
            "impressions_completed",
            "pages_completed",
            "media_sheets_completed",
        ):
            value = counters.get(key)

            if isinstance(value, (int, float)) and value >= 0:
                page_count = int(value)
                page_count_source = "ipp:" + key
                break

    markers = []

    for index, marker in enumerate(
        ipp.get("markers") or []
    ):
        item = dict(marker)
        item["index"] = index

        level = item.get("level")
        high = item.get("high_level")

        percent = None

        if isinstance(level, (int, float)) and level >= 0:

            if isinstance(high, (int, float)) and high > 0:
                percent = round(
                    level * 100 / high
                )

            elif level <= 100:
                percent = round(level)

        if percent is not None:
            percent = max(
                0,
                min(100, percent),
            )

        item["percent"] = percent

        markers.append(item)

    if not markers:
        for index, supply in enumerate(
            snmp.get("supplies") or []
        ):
            item = dict(supply)
            item["index"] = index
            markers.append(item)

    reasons = normalize_reasons(ipp.get("reasons"))

    accepting = ipp.get(
        "accepting_jobs"
    )

    return {
        "ipp": ipp,
        "snmp": snmp,

        "page_count": page_count,
        "page_count_source": page_count_source,

        "serial": (
            snmp.get("serial")
            or ipp.get("serial")
        ),

        "reasons_text": (
            ", ".join(reasons)
            if reasons
            else "none"
        ),

        "accepting_jobs": (
            "ON"
            if accepting is True
            else "OFF"
            if accepting is False
            else None
        ),

        "markers": markers,
    }


def probe_printer_sources(
    device,
    do_ipp,
    do_snmp,
    snmp_community,
):
    result = {
        "ipp_attempted": do_ipp,
        "snmp_attempted": do_snmp,
    }

    if do_ipp:
        result["ipp"] = probe_ipp(
            device["ip"]
        )

    if do_snmp:
        result["snmp"] = probe_snmp_printer(
            device["ip"],
            snmp_community,
        )

    return result


def enrich_printer_telemetry(
    devices,
    ipp_interval,
    snmp_interval,
    snmp_community,
):
    printers = []

    for device in devices:
        if (
            device.get("type") == "printer"
            or device.get("id") in PRINTER_CACHE
        ):
            device["type"] = "printer"
            printers.append(device)

    if not printers:
        return

    now = time.monotonic()

    jobs = {}

    with ThreadPoolExecutor(
        max_workers=min(
            8,
            len(printers),
        )
    ) as executor:

        for device in printers:

            cache = PRINTER_CACHE.setdefault(
                device["id"],
                {
                    "ipp": None,
                    "snmp": None,
                    "ipp_at": 0.0,
                    "snmp_at": 0.0,
                },
            )

            online = bool(
                device.get("online")
            )

            ports = device.get(
                "open_ports",
                {},
            )

            do_ipp = bool(
                online
                and "ipp" in ports
                and (
                    cache["ipp"] is None
                    or now - cache["ipp_at"]
                    >= ipp_interval
                )
            )

            do_snmp = bool(
                online
                and (
                    cache["snmp"] is None
                    or now - cache["snmp_at"]
                    >= snmp_interval
                )
            )

            if not do_ipp and not do_snmp:
                continue

            future = executor.submit(
                probe_printer_sources,
                device,
                do_ipp,
                do_snmp,
                snmp_community,
            )

            jobs[future] = device

        for future in as_completed(jobs):

            device = jobs[future]

            cache = PRINTER_CACHE[
                device["id"]
            ]

            try:
                result = future.result()

            except Exception as exc:
                log(
                    f"{device['ip']}: "
                    f"printer telemetry error: "
                    f"{exc}"
                )
                continue

            if result.get(
                "ipp_attempted"
            ):
                ipp_result = result.get(
                    "ipp"
                )

                if (
                    ipp_result
                    and ipp_result.get("ok")
                ):
                    cache["ipp"] = ipp_result
                    cache["ipp_observed_at"] = time.time()

                cache["ipp_fresh"] = bool(ipp_result and ipp_result.get("ok"))

                cache["ipp_at"] = now

            if result.get(
                "snmp_attempted"
            ):
                snmp_result = result.get(
                    "snmp"
                )

                if (
                    snmp_result
                    and snmp_result.get("ok")
                ):
                    cache["snmp"] = snmp_result
                    cache["snmp_observed_at"] = time.time()

                cache["snmp_fresh"] = bool(snmp_result and snmp_result.get("ok"))

                cache["snmp_at"] = now

    # Каждый новый device создаётся заново
    # на каждом цикле, поэтому прикрепляем
    # сохранённую телеметрию обратно.
    for device in printers:

        cache = PRINTER_CACHE.get(
            device["id"],
            {},
        )

        device["printer"] = normalize_printer_data(
            cache.get("ipp"),
            cache.get("snmp"),
        )
        for field in ("ipp_observed_at", "snmp_observed_at", "ipp_fresh", "snmp_fresh"):
            device["printer"][field] = cache.get(field)
        counter_source = device["printer"].get("page_count_source") or ""
        device["printer"]["page_count_observed_at"] = cache.get(
            "snmp_observed_at" if counter_source == "snmp" else "ipp_observed_at")
        # Last-good values survive failures; freshness is independent of value.
        device["printer"]["ipp_fresh"] = bool(
            device.get("online") and cache.get("ipp_fresh")
            and "ipp" in device.get("open_ports", {})
            and now - cache.get("ipp_at", 0) <= ipp_interval * 2)
        device["printer"]["snmp_fresh"] = bool(
            device.get("online") and cache.get("snmp_fresh")
            and now - cache.get("snmp_at", 0) <= snmp_interval * 2)

    save_printer_cache()



def classify_device(name, hostname, ports):
    text = (
        f"{name or ''} "
        f"{hostname or ''}"
    ).lower()

    # Принтер / МФУ
    if (
        {"jetdirect", "ipp", "lpd"}
        & set(ports.keys())
        or any(
            token in text
            for token in (
                "printer",
                "print",
                "принтер",
                "мфу",
                "kyocera",
                "kyosera",
                "brother",
                "epson",
                "canon",
                "xerox",
                "laserjet",
            )
        )
    ):
        return "printer"

    # Камера / NVR
    if (
        "rtsp" in ports
        or any(
            token in text
            for token in (
                "camera",
                "cam",
                "камера",
                "hikvision",
                "hiwatch",
                "dahua",
                "ihawk",
                "nvr",
                "dvr",
            )
        )
    ):
        return "camera"

    # VoIP
    if {"sip", "sips"} & set(ports) or any(
        token in text
        for token in (
            "voip",
            "yealink",
            "sip",
            "phone",
            "телефон",
        )
    ):
        return "voip"

    # Access Point
    if re.search(
        r"(^|\W)ap($|\W)",
        text,
    ):
        return "access_point"

    # Сервер / NAS
    if any(
        token in text
        for token in (
            "synology",
            "qnap",
            "nas",
            "server",
            "сервер",
            "srv",
        )
    ):
        return "server"

    # Компьютер
    if (
        "smb" in ports
        or any(
            token in text
            for token in (
                "pc",
                "комп",
                "computer",
                "desktop",
                "notebook",
                "ноут",
                "laptop",
                "workstation",
            )
        )
    ):
        return "computer"

    return "unknown"


def normalize_reasons(value):
    return [value] if isinstance(value, str) else list(value or [])


def safe_router_error(exc):
    # RouterOS exceptions may embed the entire login command, including password.
    text = str(exc).lower()
    if "invalid user" in text or "password" in text or "login" in text:
        return "authentication_failed"
    if isinstance(exc, TimeoutError) or "timed out" in text:
        return "connection_timeout"
    if isinstance(exc, OSError):
        return "network_error"
    if isinstance(exc, ValueError):
        return "invalid_configuration_or_response"
    return "router_api_error"


def configure_mqtt_recovery(client, refresh):
    def connected(client, userdata, flags, reason_code, properties):
        if reason_code == 0:
            client.publish(AVAILABILITY_TOPIC, "online", qos=1, retain=True)
            client.subscribe("homeassistant/status", qos=1)
            refresh.set()
    def message(client, userdata, message):
        if message.topic == "homeassistant/status" and message.payload == b"online":
            refresh.set()
    client.on_message = message
    client.on_connect = connected


def collect_router(site, username, password):
    pool = None

    try:
        pool = routeros_api.RouterOsApiPool(
            site["router"],
            username=username,
            password=password,
            port=8728,
            plaintext_login=True,
        )

        pool.set_timeout(10.0)
        api = pool.get_api()

        identity = (
            api
            .get_resource("/system/identity")
            .get()[0]["name"]
        )

        addresses = (
            api
            .get_resource("/ip/address")
            .get()
        )

        leases = (
            api
            .get_resource("/ip/dhcp-server/lease")
            .get()
        )

        arp = (
            api
            .get_resource("/ip/arp")
            .get()
        )

        explicit_lan = (site.get("lan") or "").strip()

        if explicit_lan:
            try:
                lan = str(
                    ipaddress.ip_network(
                        explicit_lan,
                        strict=False,
                    )
                )
            except ValueError:
                raise ValueError(
                    f"Invalid LAN network for "
                    f"{site['name']}: {explicit_lan}"
                )
        else:
            lan = detect_lan(addresses)

        arp_by_ip = {
            row.get("address"): row
            for row in arp
            if row.get("address")
        }

        diagnostics = {"leases_total": len(leases), "static_total": 0,
                       "skipped_no_ip": 0, "skipped_outside_lan": 0,
                       "skipped_invalid_ip": 0, "skipped_no_mac": 0,
                       "lan_source": "explicit" if explicit_lan else "automatic"}
        devices = []

        for lease in leases:

            # Главное правило проекта:
            #
            # dynamic DHCP lease
            #     → пользовательское устройство
            #     → в HA не публикуем
            #
            # static DHCP lease
            #     → управляемое устройство
            #     → публикуем

            if is_true(
                lease.get("dynamic")
            ):
                continue

            diagnostics["static_total"] += 1
            ip = lease.get("address")

            if not ip:
                diagnostics["skipped_no_ip"] += 1
                continue

            # Lease должен принадлежать LAN
            # конкретной студии.

            if lan:
                try:
                    if (
                        ipaddress.ip_address(ip)
                        not in ipaddress.ip_network(lan)
                    ):
                        diagnostics["skipped_outside_lan"] += 1
                        continue
                except ValueError:
                    diagnostics["skipped_invalid_ip"] += 1
                    continue

            arp_row = arp_by_ip.get(
                ip,
                {},
            )

            mac = clean_mac(
                lease.get("mac-address")
                or lease.get(
                    "active-mac-address"
                )
                or arp_row.get(
                    "mac-address"
                )
            )

            # MAC — наш стабильный identity.
            # Без него устройство пока не создаём.

            if not mac:
                diagnostics["skipped_no_mac"] += 1
                continue

            comment = (
                lease.get("comment")
                or ""
            ).strip()

            hostname = (
                lease.get("host-name")
                or lease.get(
                    "active-host-name"
                )
                or ""
            ).strip()

            name = safe_name(
                comment
                or hostname
                or f"Device {ip}"
            )

            devices.append(
                {
                    "site": site["name"],
                    "router": site["router"],
                    "ip": ip,
                    "mac": mac,
                    "id": device_id(mac),
                    "name": name,
                    "hostname": (
                        hostname
                        or None
                    ),
                    "comment": (
                        comment
                        or None
                    ),
                    "arp_status": (
                        arp_row.get(
                            "status",
                            "not-seen",
                        )
                    ),
                }
            )

        return {
            "ok": True,
            "site": site,
            "identity": identity,
            "lan": lan,
            "devices": devices,
            "diagnostics": diagnostics,
        }

    except Exception as exc:

        return {
            "ok": False,
            "site": site,
            "identity": None,
            "lan": None,
            "devices": [],
            "error": safe_router_error(exc),
        }

    finally:

        if pool:

            try:
                pool.disconnect()

            except Exception:
                pass


def collect_all_routers(
    sites,
    username,
    password,
):
    global ROUTER_POLLER
    if ROUTER_POLLER is None:
        ROUTER_POLLER = RouterPoller()
    return ROUTER_POLLER.collect(sites, collect_router, username, password)



def enrich_devices(devices):
    if not devices:
        return

    with ThreadPoolExecutor(
        max_workers=24
    ) as executor:

        jobs = {
            executor.submit(
                probe_device,
                device["ip"],
            ): device
            for device in devices
        }

        for future in as_completed(jobs):

            device = jobs[future]

            try:
                probe = future.result()

            except Exception:
                probe = {
                    "ping": False,
                    "ports": {},
                }

            device["ping"] = probe["ping"]
            device["open_ports"] = probe["ports"]

            device["type"] = classify_device(
                device["name"],
                device["hostname"],
                device["open_ports"],
            )

            # ONLINE считаем, если:
            #
            # ping отвечает
            # ИЛИ открыт известный TCP port
            # ИЛИ MikroTik видит устройство через ARP

            device["online"] = bool(
                device["ping"]
                or device["open_ports"]
                or device["arp_status"] in (
                    "reachable",
                    "delay",
                    "probe",
                    "permanent",
                )
            )


def mqtt_json(
    client,
    topic,
    payload,
    retain=True,
):
    result = client.publish(
        topic,
        json.dumps(
            payload,
            ensure_ascii=False,
        ),
        qos=1,
        retain=retain,
    )

    return result


def publish_device_discovery(
    client,
    device,
    expire_after,
):
    state_topic = (
        f"{MQTT_ROOT}/"
        f"{device['id']}/state"
    )

    config_topic = (
        f"{DISCOVERY_PREFIX}/"
        f"binary_sensor/"
        f"{device['id']}/"
        f"online/config"
    )

    device_info = {
        "identifiers": [
            device["id"]
        ],
        "connections": [
            [
                "mac",
                device["mac"],
            ]
        ],
        "name": device["name"],
        "suggested_area": (
            device["site"]
        ),
        "manufacturer": (
            "Maria Network"
        ),
        "model": device["type"],
    }

    ports = device.get(
        "open_ports",
        {},
    )

    if "https" in ports:
        device_info[
            "configuration_url"
        ] = (
            f"https://{device['ip']}"
        )

    elif "http" in ports:
        device_info[
            "configuration_url"
        ] = (
            f"http://{device['ip']}"
        )

    mqtt_json(
        client,
        config_topic,
        {
            "name": "Online",

            "unique_id": (
                device["id"]
                + "_online"
            ),

            "state_topic": (
                state_topic
            ),

            "value_template": (
                "{{ value_json.online }}"
            ),

            "payload_on": "ON",
            "payload_off": "OFF",

            "device_class": (
                "connectivity"
            ),

            "expire_after": max(
                expire_after,
                900,
            ),

            "availability_topic": (
                AVAILABILITY_TOPIC
            ),

            "payload_available": (
                "online"
            ),

            "payload_not_available": (
                "offline"
            ),

            "json_attributes_topic": (
                state_topic
            ),

            "device": device_info,
        },
    )


def _printer_device_info(device):
    data = device.get("printer") or {}
    ipp = data.get("ipp") or {}

    info = {
        "identifiers": [
            device["id"]
        ],

        "connections": [
            [
                "mac",
                device["mac"],
            ]
        ],

        "name": device["name"],

        "suggested_area": (
            device["site"]
        ),

        "manufacturer": (
            ipp.get("manufacturer")
            or "Maria Network"
        ),

        "model": (
            ipp.get("model")
            or "printer"
        ),
    }

    if data.get("serial"):
        info["serial_number"] = str(
            data["serial"]
        )

    ports = device.get(
        "open_ports",
        {},
    )

    if "https" in ports:
        info["configuration_url"] = (
            f"https://{device['ip']}"
        )

    elif "http" in ports:
        info["configuration_url"] = (
            f"http://{device['ip']}"
        )

    return info


def _printer_discovery_topic(
    device_id,
    component,
    object_id,
):
    return (
        f"{DISCOVERY_PREFIX}/"
        f"{component}/"
        f"{device_id}/"
        f"{object_id}/config"
    )


def publish_printer_discovery(
    client,
    device,
    expire_after,
):
    if device.get("type") != "printer":
        return

    data = device.get("printer") or {}
    ipp = data.get("ipp") or {}

    state_topic = (
        f"{MQTT_ROOT}/"
        f"{device['id']}/state"
    )

    common = {
        "state_topic": state_topic,

        "expire_after": max(
            expire_after,
            900,
        ),

        "availability_topic": (
            AVAILABILITY_TOPIC
        ),

        "payload_available": "online",
        "payload_not_available": "offline",

        "device": (
            _printer_device_info(
                device
            )
        ),
    }

    # --------------------------
    # IPP status sensors
    # --------------------------

    if ipp.get("ok"):

        sensors = (
            (
                "printer_state",
                "Printer State",
                "{{ value_json.printer.ipp.state }}",
            ),
            (
                "printer_reasons",
                "Printer Reasons",
                "{{ value_json.printer.reasons_text }}",
            ),
            (
                "printer_queue",
                "Print Queue",
                "{{ value_json.printer.ipp.queued_jobs }}",
            ),
        )

        for (
            object_id,
            name,
            template,
        ) in sensors:

            mqtt_json(
                client,

                _printer_discovery_topic(
                    device["id"],
                    "sensor",
                    object_id,
                ),

                {
                    **common,

                    "name": name,

                    "unique_id": (
                        f"{device['id']}_"
                        f"{object_id}"
                    ),

                    "value_template": (
                        template
                    ),

                    "entity_category": (
                        "diagnostic"
                    ),
                },
            )

        if ipp.get("uptime") is not None:

            mqtt_json(
                client,

                _printer_discovery_topic(
                    device["id"],
                    "sensor",
                    "printer_uptime",
                ),

                {
                    **common,

                    "name": (
                        "Printer Uptime"
                    ),

                    "unique_id": (
                        device["id"]
                        + "_printer_uptime"
                    ),

                    "value_template": (
                        "{{ value_json.printer.ipp.uptime }}"
                    ),

                    "device_class": (
                        "duration"
                    ),

                    "unit_of_measurement": (
                        "s"
                    ),

                    "state_class": (
                        "measurement"
                    ),

                    "entity_category": (
                        "diagnostic"
                    ),
                },
            )

        mqtt_json(
            client,

            _printer_discovery_topic(
                device["id"],
                "binary_sensor",
                "accepting_jobs",
            ),

            {
                **common,

                "name": (
                    "Accepting Jobs"
                ),

                "unique_id": (
                    device["id"]
                    + "_accepting_jobs"
                ),

                "value_template": (
                    "{{ value_json.printer.accepting_jobs }}"
                ),

                "payload_on": "ON",
                "payload_off": "OFF",

                "entity_category": (
                    "diagnostic"
                ),
            },
        )

    # --------------------------
    # Пробег
    # --------------------------

    if data.get("page_count") is not None:

        mqtt_json(
            client,

            _printer_discovery_topic(
                device["id"],
                "sensor",
                "page_count",
            ),

            {
                **common,

                "name": (
                    "Page Counter"
                ),

                "unique_id": (
                    device["id"]
                    + "_page_count"
                ),

                "value_template": (
                    "{{ value_json.printer.page_count }}"
                ),

                "unit_of_measurement": (
                    "pages"
                ),

                "state_class": (
                    "total_increasing"
                ),

                "icon": "mdi:counter",
            },
        )

    # --------------------------
    # Расходники
    # --------------------------

    used = set()

    for marker in (
        data.get("markers")
        or []
    ):

        index = marker["index"]

        if index >= 16:
            break

        if marker.get(
            "percent"
        ) is None:
            continue

        used.add(index)

        name = (
            marker.get("name")
            or marker.get("type")
            or f"Consumable {index + 1}"
        )

        if len(name) > 50:
            name = (
                str(
                    marker.get("type")
                    or "Consumable"
                )
                + f" {index + 1}"
            )

        mqtt_json(
            client,

            _printer_discovery_topic(
                device["id"],
                "sensor",
                f"marker_{index}",
            ),

            {
                **common,

                "name": name,

                "unique_id": (
                    f"{device['id']}_"
                    f"marker_{index}"
                ),

                "value_template": (
                    "{{ value_json.printer."
                    f"markers[{index}].percent }}}}"
                ),

                "unit_of_measurement": "%",

                "state_class": (
                    "measurement"
                ),

                "icon": "mdi:printer",
            },
        )

    # Если расходников стало меньше,
    # старые MQTT Discovery entities удаляем.

    for index in range(16):

        if index in used:
            continue

        client.publish(
            _printer_discovery_topic(
                device["id"],
                "sensor",
                f"marker_{index}",
            ),
            "",
            qos=1,
            retain=True,
        )


def publish_device_state(
    client,
    device,
):
    mqtt_json(
        client,
        (
            f"{MQTT_ROOT}/"
            f"{device['id']}/state"
        ),
        {
            "online": (
                "ON"
                if device["online"]
                else "OFF"
            ),

            "site": (
                device["site"]
            ),

            "ip": (
                device["ip"]
            ),

            "mac": (
                device["mac"]
            ),

            "type": (
                device["type"]
            ),

            "printer": (
                device.get("printer")
            ),

            "hostname": (
                device["hostname"]
            ),

            "arp_status": (
                device["arp_status"]
            ),

            "ping": (
                device["ping"]
            ),

            "open_ports": (
                device["open_ports"]
            ),

            "router": (
                device["router"]
            ),

            "source": (
                "MikroTik static DHCP lease"
            ),
        },
    )


def publish_router_discovery(
    client,
    result,
    expire_after,
):
    site = result["site"]

    slug = site_slug(
        site["name"]
    )

    router_id = (
        f"maria_router_{slug}"
    )

    state_topic = (
        f"{MQTT_ROOT}/"
        f"router/{slug}/state"
    )

    config_topic = (
        f"{DISCOVERY_PREFIX}/"
        f"binary_sensor/"
        f"{router_id}/"
        f"online/config"
    )

    mqtt_json(
        client,
        config_topic,
        {
            "name": "Online",

            "unique_id": (
                router_id
                + "_online"
            ),

            "state_topic": (
                state_topic
            ),

            "value_template": (
                "{{ value_json.online }}"
            ),

            "payload_on": "ON",
            "payload_off": "OFF",

            "device_class": (
                "connectivity"
            ),

            "expire_after": (
                expire_after
            ),

            "availability_topic": (
                AVAILABILITY_TOPIC
            ),

            "payload_available": (
                "online"
            ),

            "payload_not_available": (
                "offline"
            ),

            "json_attributes_topic": (
                state_topic
            ),

            "device": {
                "identifiers": [
                    router_id
                ],

                "name": (
                    result.get(
                        "identity"
                    )
                    or site["name"]
                ),

                "suggested_area": (
                    site["name"]
                ),

                "manufacturer": (
                    "MikroTik"
                ),

                "model": (
                    "RouterOS gateway"
                ),
            },
        },
    )


def publish_router_state(
    client,
    result,
):
    site = result["site"]

    slug = site_slug(
        site["name"]
    )

    mqtt_json(
        client,
        (
            f"{MQTT_ROOT}/"
            f"router/{slug}/state"
        ),
        {
            "online": (
                "ON"
                if result["ok"]
                else "OFF"
            ),

            "site": site["name"],

            "router": (
                site["router"]
            ),

            "identity": (
                result.get(
                    "identity"
                )
            ),

            "lan": (
                result.get("lan")
            ),

            "managed_devices": (
                len(
                    result.get(
                        "devices",
                        [],
                    )
                )
            ),

            "error": (
                result.get(
                    "error"
                )
            ),

            "source": (
                "RouterOS API"
            ),
        },
    )


def publish_monitor_discovery(
    client,
    expire_after,
):
    config_topic = (
        f"{DISCOVERY_PREFIX}/"
        f"binary_sensor/"
        f"maria_network_monitor/"
        f"online/config"
    )

    mqtt_json(
        client,
        config_topic,
        {
            "name": "Online",

            "unique_id": (
                "maria_network_monitor_online"
            ),

            "state_topic": (
                f"{MQTT_ROOT}/"
                f"monitor/state"
            ),

            "payload_on": "ON",
            "payload_off": "OFF",

            "device_class": (
                "connectivity"
            ),

            "expire_after": (
                expire_after
            ),

            "availability_topic": (
                AVAILABILITY_TOPIC
            ),

            "payload_available": (
                "online"
            ),

            "payload_not_available": (
                "offline"
            ),

            "device": {
                "identifiers": [
                    "maria_network_monitor"
                ],

                "name": (
                    "Maria Network Monitor"
                ),

                "manufacturer": (
                    "Maria Network"
                ),

                "model": (
                    "MikroTik → MQTT → Home Assistant"
                ),
            },
        },
    )


def remove_device(
    client,
    old_device_id,
):
    discovery_topic = (
        f"{DISCOVERY_PREFIX}/"
        f"binary_sensor/"
        f"{old_device_id}/"
        f"online/config"
    )

    state_topic = (
        f"{MQTT_ROOT}/"
        f"{old_device_id}/state"
    )

    client.publish(
        discovery_topic,
        "",
        qos=1,
        retain=True,
    )

    for component, object_id in (
        ("sensor", "printer_state"),
        ("sensor", "printer_reasons"),
        ("sensor", "printer_queue"),
        ("sensor", "printer_uptime"),
        ("sensor", "page_count"),
        ("binary_sensor", "accepting_jobs"),
    ):

        client.publish(
            _printer_discovery_topic(
                old_device_id,
                component,
                object_id,
            ),
            "",
            qos=1,
            retain=True,
        )

    for index in range(16):

        client.publish(
            _printer_discovery_topic(
                old_device_id,
                "sensor",
                f"marker_{index}",
            ),
            "",
            qos=1,
            retain=True,
        )

    client.publish(
        state_topic,
        "",
        qos=1,
        retain=True,
    )


def connect_mqtt(client, options):
    while not STOP.is_set():

        try:
            log(
                "Connecting MQTT "
                f"{options['mqtt_host']}:"
                f"{options['mqtt_port']}..."
            )

            client.connect(
                options["mqtt_host"],
                int(
                    options["mqtt_port"]
                ),
                60,
            )

            log("MQTT connected")

            return

        except Exception as exc:

            log(
                f"MQTT connection error: {exc}"
            )

            STOP.wait(10)

    raise SystemExit(
        "Stopped before MQTT connection"
    )


def main():
    options = load_json(
        OPTIONS_FILE
    )

    if not options:
        raise SystemExit(
            "Cannot read /data/options.json"
        )

    required = (
        "mikrotik_username",
        "mikrotik_password",
        "mqtt_host",
        "mqtt_port",
        "mqtt_username",
        "mqtt_password",
        "sites",
    )

    for key in required:

        if options.get(key) in (
            None,
            "",
        ):
            raise SystemExit(
                f"Missing option: {key}"
            )

    sites = options.get("sites") or []

    if not sites:
        raise SystemExit(
            "No sites configured. Add at least one "
            "MikroTik in the app configuration."
        )

    # Legacy router MQTT IDs remain unchanged; ambiguous legacy IDs fail clearly.
    slugs = [site_slug(site["name"]) for site in sites]
    if len(set(slugs)) != len(slugs):
        raise SystemExit("Site names must have distinct legacy ASCII slugs. "
                         "Existing MQTT identifiers are preserved; see DOCS.md.")

    dashboard_enabled = bool(options.get("dashboard_enabled", True))
    inventory = load_json(INVENTORY_FILE, {"sites": {}}) or {"sites": {}}
    report_timezone = options.get("report_timezone") or os.environ.get("TZ") or "UTC"
    try:
        report_state = load_json(REPORT_FILE, {}) or {}
        # Never silently relabel old daily buckets or overwrite corrupt report files.
        update_reports(report_state, [], report_timezone)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        report_state = None
        log("Daily reports disabled: check ledger integrity and report_timezone; monitoring continues")
    dashboard_topics = set()
    printer_topics = set()
    if dashboard_enabled:
        # Always keep a portable fallback, independently of the HA config mount.
        for directory in (Path("/data/dashboard"), Path("/share/maria_network_monitor"),
                          Path("/homeassistant/maria_network_monitor")):
            if directory.parts[1] == "homeassistant" and not Path("/homeassistant").is_dir():
                continue
            if directory.parts[1] == "share" and not Path("/share").is_dir():
                continue
            try:
                export_dashboard(sites, directory)
                if directory.parts[1] == "homeassistant":
                    export_assets(Path("/homeassistant/www/maria_network_monitor"))
                else:
                    export_assets(directory)
                log("Dashboard files ready: " + str(directory))
            except (OSError, ValueError) as exc:
                log("Dashboard export unavailable; monitoring continues: " + str(exc))

    state_interval = int(
        options.get(
            "state_interval",
            60,
        )
    )

    discovery_interval = int(
        options.get(
            "discovery_interval",
            600,
        )
    )

    expire_after = int(
        options.get(
            "expire_after",
            180,
        )
    )

    printer_ipp_interval = int(
        options.get(
            "printer_ipp_interval",
            60,
        )
    )

    printer_snmp_interval = int(
        options.get(
            "printer_snmp_interval",
            600,
        )
    )

    snmp_community = str(
        options.get(
            "snmp_community",
            "public",
        )
    )

    known = load_json(
        KNOWN_FILE,
        {
            "sites": {}
        },
    )

    if "sites" not in known:
        known["sites"] = {}

    restore_printer_cache()

    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=(
            "maria-network-monitor"
        ),
    )

    mqtt_refresh = threading.Event()
    configure_mqtt_recovery(client, mqtt_refresh)
    client = BoundedPublisher(client, mqtt_refresh, mqtt.MQTT_ERR_QUEUE_SIZE)

    client.username_pw_set(
        options["mqtt_username"],
        options["mqtt_password"],
    )

    # Если контейнер неожиданно умер,
    # Mosquitto сам объявит монитор offline.

    client.will_set(
        AVAILABILITY_TOPIC,
        "offline",
        qos=1,
        retain=True,
    )

    client.reconnect_delay_set(
        min_delay=1,
        max_delay=30,
    )

    connect_mqtt(
        client,
        options,
    )

    client.loop_start()

    client.publish(
        AVAILABILITY_TOPIC,
        "online",
        qos=1,
        retain=True,
    )

    publish_monitor_discovery(
        client,
        expire_after,
    )

    if dashboard_enabled:
        configured_site_keys = {site_key(site["name"]) for site in sites}
        for old_key, old_site in inventory.get("sites", {}).items():
            if old_key not in configured_site_keys:
                # Clean only Maria's additive summaries, never user dashboards.
                for topic, _ in discovery_configs(old_site, expire_after):
                    client.publish(topic, "", qos=1, retain=True)
                client.publish(f"{MQTT_ROOT}/dashboard/{old_key}/state", "", qos=1, retain=True)
                report_topic, _ = report_discovery(old_site)
                client.publish(report_topic, "", qos=1, retain=True)
                client.publish(f"{MQTT_ROOT}/reports/{old_key}/state", "", qos=1, retain=True)

    last_discovery = 0.0
    recovery_sites = {site["name"] for site in sites}

    log(
        "Maria Network Monitor started"
    )

    while not STOP.is_set():

        client.begin_cycle()
        if mqtt_refresh.is_set() and client.is_connected():
            mqtt_refresh.clear()
            last_discovery = 0.0
            recovery_sites = {site["name"] for site in sites}
            printer_topics.clear()
            dashboard_topics.clear()
            publish_monitor_discovery(client, expire_after)

        cycle_started = (
            time.monotonic()
        )

        do_discovery = (
            bool(recovery_sites) or last_discovery == 0
            or (
                cycle_started
                - last_discovery
                >= discovery_interval
            )
        )

        # -----------------------------
        # 1. Читаем MikroTik
        # -----------------------------

        results = collect_all_routers(
            sites,
            options[
                "mikrotik_username"
            ],
            options[
                "mikrotik_password"
            ],
        )

        confirm_absences(inventory, results)

        devices = [
            device
            for result in results
            if result["ok"]
            for device
            in result["devices"]
        ]

        # -----------------------------
        # 2. Проверяем устройства
        # -----------------------------

        enrich_devices(
            devices
        )

        previous_types = {item["id"]: item.get("type", "unknown")
                          for saved_site in inventory.get("sites", {}).values()
                          for item in saved_site.get("devices", [])}
        for device in devices:
            if device["type"] == "unknown" and device["id"] in previous_types:
                device["type"] = previous_types[device["id"]]

        enrich_printer_telemetry(
            devices,
            printer_ipp_interval,
            printer_snmp_interval,
            snmp_community,
        )

        # -----------------------------
        # 3. MQTT Discovery
        # -----------------------------

        if do_discovery:

            first_discovery = (
                last_discovery == 0
            )

            # Не перепубликуем MQTT Discovery
            # существующих роутеров каждые N минут.
            # Home Assistant при обновлении discovery
            # кратковременно делает entity unavailable.
            if first_discovery:

                for result in results:

                    publish_router_discovery(
                        client,
                        result,
                        expire_after,
                    )

            for result in results:

                # Если RouterOS API сейчас
                # недоступен — ничего из HA
                # не удаляем.
                #
                # Иначе временная проблема VPN
                # выглядела бы как удаление
                # всех устройств студии.

                if not result["ok"]:
                    continue

                site_name = (
                    result["site"]["name"]
                )

                current_ids = {
                    device["id"]
                    for device
                    in result["devices"]
                }

                previous_ids = set(
                    known["sites"].get(
                        site_name,
                        [],
                    )
                )

                current_ids.update(result.get("pending_ids", set()))

                added_ids = (
                    current_ids
                    - previous_ids
                )

                for device in (
                    result["devices"]
                ):

                    # При старте восстанавливаем Discovery
                    # для всех существующих устройств.
                    #
                    # Во время работы Discovery публикуем
                    # только для новых static DHCP leases.
                    if (
                        site_name in recovery_sites
                        or device["id"] in added_ids
                    ):

                        publish_device_discovery(
                            client,
                            device,
                            expire_after,
                        )

                        publish_printer_discovery(
                            NewPrinterDiscovery(client, printer_topics),
                            device,
                            expire_after,
                        )

                # Static DHCP lease исчез
                # с MikroTik →
                # устройство больше
                # не является managed.

                removed = (
                    previous_ids
                    - current_ids
                )

                for old_id in removed:

                    remove_device(
                        client,
                        old_id,
                    )
                    printer_topics.difference_update(
                        topic for topic in list(printer_topics) if f"/{old_id}/" in topic)

                    log(
                        f"{site_name}: "
                        f"removed {old_id}"
                    )

                known["sites"][
                    site_name
                ] = sorted(
                    current_ids
                )
                if not result.get("pending_ids"):
                    recovery_sites.discard(site_name)

            save_json(
                KNOWN_FILE,
                known,
            )

            last_discovery = (
                cycle_started
            )

            log(
                "MQTT Discovery refreshed"
            )

        # -----------------------------
        # 4. Обновляем состояния
        # -----------------------------

        for result in results:

            publish_router_state(
                client,
                result,
            )

        for device in devices:

            publish_device_state(
                client,
                device,
            )

            # New metrics can arrive after the lease's initial discovery.
            # Forward only previously unseen config topics to avoid flapping.
            publish_printer_discovery(
                NewPrinterDiscovery(client, printer_topics), device, expire_after)

        # This layer never controls existing discovery topics or HA dashboards.
        inventory = update_inventory(inventory, results)
        for saved_site in inventory["sites"].values():
            saved_site["router_unique_id"] = "maria_router_" + site_slug(saved_site["name"]) + "_online"
        if report_state is not None:
            update_reports(report_state, devices, report_timezone)
            try:
                report_state["persistence_ok"] = True
                save_json(REPORT_FILE, report_state)
            except OSError:
                report_state["persistence_ok"] = False
                log("Daily report persistence failed; current report may not survive restart")
        try:
            save_json(INVENTORY_FILE, inventory)
        except OSError as exc:
            log("Inventory save failed; monitoring continues: " + str(exc))
        if dashboard_enabled:
            for saved_site in inventory["sites"].values():
                saved_site["freshness_seconds"] = max(180, state_interval * 3)
                if report_state is not None:
                    report = site_report(report_state, saved_site, stale_after=max(1800, printer_snmp_interval * 3))
                    report_topic, config = report_discovery(saved_site)
                    if report_topic not in dashboard_topics:
                        mqtt_json(client, report_topic, config)
                        dashboard_topics.add(report_topic)
                    mqtt_json(client, f"{MQTT_ROOT}/reports/{saved_site['site_id']}/state", report)
                for topic, config in discovery_configs(saved_site, expire_after):
                    if topic not in dashboard_topics:
                        mqtt_json(client, topic, config)
                        dashboard_topics.add(topic)
                mqtt_json(client, f"{MQTT_ROOT}/dashboard/{saved_site['site_id']}/state", saved_site)

        # heartbeat нашего монитора

        client.publish(
            (
                f"{MQTT_ROOT}/"
                f"monitor/state"
            ),
            "ON",
            qos=1,
            retain=True,
        )

        # -----------------------------
        # 5. Лог цикла
        # -----------------------------

        routers_ok = sum(
            1
            for result in results
            if result["ok"]
        )

        online_count = sum(
            1
            for device in devices
            if device["online"]
        )

        offline_count = (
            len(devices)
            - online_count
        )

        for result in results:

            site_name = (
                result["site"]["name"]
            )

            if result["ok"]:

                log(
                    f"{site_name}: "
                    f"API OK, "
                    f"{len(result['devices'])} "
                    f"accepted static; filters={result.get('diagnostics', {})}"
                )

            else:

                log(
                    f"{site_name}: "
                    f"API ERROR: "
                    f"{result.get('error')}"
                )

        duration = (
            time.monotonic()
            - cycle_started
        )

        log(
            "CYCLE: "
            f"routers "
            f"{routers_ok}/"
            f"{len(results)}, "
            f"devices "
            f"{len(devices)}, "
            f"online "
            f"{online_count}, "
            f"offline "
            f"{offline_count}, "
            f"time "
            f"{duration:.1f}s"
        )

        # Цикл стараемся держать
        # ровно state_interval секунд.

        wait_time = max(
            1,
            state_interval
            - duration,
        )

        STOP.wait(
            wait_time
        )

    # Нормальное завершение.
    # При аварии MQTT Last Will
    # сделает это за нас.

    log(
        "Stopping Maria Network Monitor"
    )

    try:
        client.publish(
            AVAILABILITY_TOPIC,
            "offline",
            qos=1,
            retain=True,
        ).wait_for_publish(
            timeout=2
        )

    except Exception:
        pass

    client.disconnect()
    client.loop_stop()
    if ROUTER_POLLER is not None:
        ROUTER_POLLER.close()


if __name__ == "__main__":

    signal.signal(
        signal.SIGTERM,
        lambda *_: STOP.set(),
    )

    signal.signal(
        signal.SIGINT,
        lambda *_: STOP.set(),
    )

    main()
