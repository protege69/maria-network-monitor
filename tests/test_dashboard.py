"""Synthetic-only regression tests; no router, broker or HA credentials."""
import importlib.util
import json
import sys
import tempfile
import unittest
import shutil
import uuid
from contextlib import contextmanager, ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

APP = Path(__file__).resolve().parents[1] / "maria_network_monitor"
sys.path.insert(0, str(APP))
import dashboard as d


@contextmanager
def temporary_directory():
    # Avoid Windows sandbox ACL incompatibility with tempfile's POSIX 0700.
    base = Path(tempfile.gettempdir()).resolve()
    path = base / ("maria-test-" + uuid.uuid4().hex)
    path.mkdir(mode=0o777)
    try:
        yield str(path)
    finally:
        if not path.resolve().is_relative_to(base):
            raise RuntimeError("Temporary test path escaped its root")
        shutil.rmtree(path)


def device(**changes):
    return {"id": "maria_020000000001", "name": "Demo printer", "type": "printer",
            "mac": "02:00:00:00:00:01", "site": "Demo branch", "ip": "192.0.2.10",
            "online": True, "printer": {}, **changes}


def result(ok=True, devices=None, name="Demo branch"):
    return {"site": {"name": name}, "ok": ok,
            "devices": [device()] if devices is None else devices}


class ModelTests(unittest.TestCase):
    def test_warning_boundary_and_waste(self):
        for percent, warning in [(0, True), (20, True), (21, False), (None, False), (-1, False)]:
            health = d.printer_health(device(printer={"markers": [
                {"type": "toner-cartridge", "percent": percent},
                {"name": "Waste toner", "percent": 0},
                {"name": "OPC drum", "percent": 5}]}))
            self.assertEqual(health["supplies"][0]["warning"], warning)
            self.assertFalse(health["supplies"][1]["warning"])
            self.assertFalse(health["supplies"][2]["warning"])

    def test_problem_count_is_per_printer(self):
        faulty = device(online=False, printer={"ipp": {"state": "stopped"}, "markers": [
            {"type": "toner", "percent": 10}, {"type": "toner", "percent": 20}]})
        site = next(iter(d.update_inventory({}, [result(devices=[faulty])])["sites"].values()))
        self.assertEqual(site["printer_problems"], 1)

    def test_idle_and_processing_are_not_faults(self):
        for state in ("idle", "processing", 3, 4, None):
            self.assertFalse(d.printer_health(device(printer={"ipp": {"state": state, "reasons": ["none"]}}))["problem"])

    def test_waste_full_reason_is_fault(self):
        self.assertTrue(d.printer_health(device(printer={"ipp": {"reasons": ["waste-toner-receptacle-full"]}}))["problem"])

    def test_snmp_drum_and_waste_supplement_ipp_toner(self):
        health = d.printer_health(device(printer={"markers": [{"type": "toner", "percent": 75}],
            "snmp": {"supplies": [{"name": "OPC", "percent": 40}, {"name": "Waste toner", "percent": 5},
                                   {"name": "Black toner", "percent": 70}]}}))
        self.assertEqual([m["kind"] for m in health["supplies"]], ["toner", "drum", "waste-toner"])

    def test_failure_retains_inventory_and_marks_counts_unknown(self):
        first = d.update_inventory({}, [result()], "first")
        failed = d.update_inventory(first, [result(False, [])], "next")
        site = next(iter(failed["sites"].values()))
        self.assertEqual(site["devices"], next(iter(first["sites"].values()))["devices"])
        self.assertTrue(site["stale"])
        self.assertIsNone(site["online"])
        self.assertEqual(site["last_success_at"], "first")

    def test_success_can_remove_device(self):
        first = d.update_inventory({}, [result()])
        site = next(iter(d.update_inventory(first, [result(devices=[])])["sites"].values()))
        self.assertEqual(site["devices"], [])

    def test_restart_preserves_classification(self):
        first = d.update_inventory({}, [result(devices=[device(type="camera")])])
        restored = json.loads(json.dumps(first))
        second = d.update_inventory(restored, [result(devices=[device(type="unknown", online=False)])])
        self.assertEqual(next(iter(second["sites"].values()))["devices"][0]["type"], "camera")

    def test_duplicate_lease_rows_do_not_duplicate_cards(self):
        site = next(iter(d.update_inventory({}, [result(devices=[device(), device()])])["sites"].values()))
        self.assertEqual(site["total"], 1)

    def test_site_keys_are_distinct_and_support_unicode(self):
        self.assertNotEqual(d.site_key("Demo A"), d.site_key("Demo-A"))
        self.assertNotEqual(d.site_key("Тест 1"), d.site_key("Тест 2"))

    def test_new_lease_appears_without_changing_yaml(self):
        sites = [{"name": "Demo branch"}]
        before = d.dashboard_config(sites)
        inventory = d.update_inventory({}, [result(devices=[device(), device(id="maria_020000000002")])])
        self.assertEqual(next(iter(inventory["sites"].values()))["total"], 2)
        self.assertEqual(before, d.dashboard_config(sites))

    def test_stats_unavailable_when_router_stale(self):
        site = next(iter(d.update_inventory({}, [result(False, [])])["sites"].values()))
        configs = list(d.discovery_configs(site, 180))
        self.assertEqual(len(configs), 4)
        self.assertNotIn("availability_topic", configs[0][1])
        self.assertNotIn("expire_after", configs[0][1])
        self.assertEqual(configs[1][1]["availability_mode"], "all")

    def test_no_private_connection_data_in_dashboard_inventory(self):
        item = device(ip="192.0.2.10", mac="02:00:00:00:00:01", comment="password: DEMO_SECRET")
        serialized = json.dumps(d.update_inventory({}, [result(devices=[item])]))
        self.assertNotIn("192.0.2.10", serialized)
        self.assertNotIn("DEMO_SECRET", serialized)


class ExportTests(unittest.TestCase):
    def test_idempotent_export_and_foreign_file_guard(self):
        with temporary_directory() as tmp:
            self.assertTrue(d.export_dashboard([{"name": "Demo branch"}], tmp))
            path = Path(tmp) / "maria-network-monitor.yaml"
            stamp = path.stat().st_mtime_ns
            self.assertFalse(d.export_dashboard([{"name": "Demo branch"}], tmp))
            self.assertEqual(stamp, path.stat().st_mtime_ns)
            path.write_text("views: []", encoding="utf-8")
            with self.assertRaises(ValueError):
                d.export_dashboard([{"name": "Demo branch"}], tmp)
            self.assertEqual(path.read_text(), "views: []")

    def test_portable_assets_and_yaml(self):
        with temporary_directory() as tmp:
            d.export_dashboard([{"name": "Demo branch"}], tmp)
            self.assertTrue(d.export_assets(tmp))
            self.assertFalse(d.export_assets(tmp))
            text = (Path(tmp) / "maria-network-monitor.yaml").read_text(encoding="utf-8")
            config = json.loads(text.split("\n", 1)[1])
            self.assertEqual(config["title"], "Maria Network Monitor")
            self.assertEqual(len(config["views"]), 3)
            self.assertNotIn('"gauge"', text)

    def test_new_metrics_publish_once_and_missing_metrics_do_not_remove(self):
        client = Mock()
        known = set()
        proxy = d.NewPrinterDiscovery(client, known)
        proxy.publish("page_count/config", "payload", qos=1, retain=True)
        proxy.publish("page_count/config", "payload", qos=1, retain=True)
        proxy.publish("marker_0/config", "", qos=1, retain=True)
        proxy.publish("marker_0/config", "new", qos=1, retain=True)
        self.assertEqual(client.publish.call_count, 2)


class LegacyRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Network libraries are stubbed; original collector code is exercised offline.
        for name in ("paho", "paho.mqtt", "paho.mqtt.client", "routeros_api", "pyipp"):
            sys.modules.setdefault(name, Mock())
        spec = importlib.util.spec_from_file_location("monitor_test", APP / "maria_monitor.py")
        cls.m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.m)

    def test_legacy_online_discovery_identity(self):
        client = Mock()
        self.m.publish_device_discovery(client, device(mac="02:00:00:00:00:01", ip="192.0.2.10", site="Demo branch"), 180)
        topic, payload = client.publish.call_args.args
        self.assertEqual(topic, "homeassistant/binary_sensor/maria_020000000001/online/config")
        self.assertEqual(json.loads(payload)["unique_id"], "maria_020000000001_online")

    def test_sip_phones_are_grouped_as_voip(self):
        for name, ports in [("SIP T31", {}), ("Телефон SIP", {}), ("Yealink desk", {}),
                            ("Endpoint", {"sip": 5060}), ("Endpoint", {"sips": 5061})]:
            self.assertEqual(self.m.classify_device(name, "", ports), "voip")

    def test_late_printer_metrics_without_discovery_flapping(self):
        client = Mock()
        proxy = d.NewPrinterDiscovery(client, set())
        self.m.publish_printer_discovery(proxy, device(printer={}), 180)
        self.assertEqual(client.publish.call_count, 0)
        richer = device(printer={"page_count": 100, "markers": [{"index": 0, "type": "toner", "percent": 20}]})
        self.m.publish_printer_discovery(proxy, richer, 180)
        self.assertEqual(client.publish.call_count, 2)
        self.m.publish_printer_discovery(proxy, richer, 180)
        self.assertEqual(client.publish.call_count, 2)

    def test_printer_last_good_cache_survives_failed_probe(self):
        m = self.m
        m.PRINTER_CACHE.clear()
        m.PRINTER_CACHE["maria_020000000001"] = {"ipp": {"ok": True, "state": "idle"},
            "snmp": {"ok": True, "page_count": 100}, "ipp_at": 0, "snmp_at": 0}
        item = device(ip="192.0.2.10", open_ports={"ipp": 631})
        with patch.object(m, "probe_printer_sources", return_value={"ipp_attempted": True, "snmp_attempted": True,
             "ipp": {"ok": False}, "snmp": {"ok": False}}), patch.object(m, "save_printer_cache"):
            m.enrich_printer_telemetry([item], 60, 600, "public")
        self.assertEqual(item["printer"]["page_count"], 100)
        self.assertEqual(item["printer"]["ipp"]["state"], "idle")
        self.assertFalse(item["printer"]["ipp_fresh"])

    def test_dynamic_leases_are_never_managed(self):
        pool = Mock()
        resources = {
            "/system/identity": [{"name": "Demo router"}], "/ip/address": [], "/ip/arp": [],
            "/ip/dhcp-server/lease": [
                {"dynamic": "true", "address": "192.0.2.20", "mac-address": "02:00:00:00:00:20"},
                {"dynamic": "false", "address": "192.0.2.21", "mac-address": "02:00:00:00:00:21"}],
        }
        pool.get_api.return_value.get_resource.side_effect = lambda name: Mock(get=lambda: resources[name])
        with patch.object(self.m.routeros_api, "RouterOsApiPool", return_value=pool):
            output = self.m.collect_router({"name": "Demo branch", "router": "192.0.2.1", "lan": "192.0.2.0/24"}, "demo", "demo")
        self.assertTrue(output["ok"])
        self.assertEqual(len(output["devices"]), 1)
        self.assertEqual(output["devices"][0]["id"], "maria_020000000021")
        self.assertEqual(output["diagnostics"]["leases_total"], 2)
        self.assertEqual(output["diagnostics"]["static_total"], 1)
        self.assertEqual(output["diagnostics"]["lan_source"], "explicit")

    def test_full_cycles_keep_inventory_on_outage_without_periodic_discovery(self):
        m = self.m
        item = device(router="192.0.2.1", hostname=None, arp_status="reachable", ping=True, open_ports={})
        site = {"name": "Demo branch", "router": "192.0.2.1"}
        success = {"site": site, "ok": True, "identity": "Demo router", "devices": [item]}
        failure = {"site": site, "ok": False, "devices": []}
        empty = {"site": site, "ok": True, "devices": []}
        options = {"mikrotik_username": "demo", "mikrotik_password": "demo", "mqtt_host": "demo",
                   "mqtt_port": 1883, "mqtt_username": "demo", "mqtt_password": "demo", "sites": [site]}
        client = Mock()
        stop = Mock()
        stop.is_set.side_effect = [False, False, False, True]
        def load(path, default=None):
            if path == m.OPTIONS_FILE:
                return options
            return json.loads(json.dumps(default))
        with ExitStack() as stack:
            for name, replacement in [("STOP", stop), ("load_json", load), ("log", Mock()),
                                     ("save_json", Mock()), ("restore_printer_cache", Mock()),
                                     ("enrich_devices", Mock()), ("enrich_printer_telemetry", Mock()),
                                     ("connect_mqtt", Mock()), ("export_dashboard", Mock()), ("export_assets", Mock())]:
                stack.enter_context(patch.object(m, name, replacement))
            stack.enter_context(patch.object(m.mqtt, "Client", return_value=client))
            stack.enter_context(patch.object(m, "collect_all_routers", side_effect=[[success], [failure], [empty]]))
            stack.enter_context(patch.object(m.time, "monotonic", side_effect=[1, 2, 701, 702, 1401, 1402]))
            m.main()
        published = [(call.args[0], call.args[1]) for call in client.publish.call_args_list]
        snapshots = [json.loads(payload) for topic, payload in published if "/dashboard/" in topic and topic.endswith("/state") and payload]
        self.assertEqual(len(snapshots), 3)
        self.assertTrue(snapshots[1]["stale"])
        self.assertEqual(len(snapshots[1]["devices"]), 1)
        self.assertEqual(len(snapshots[2]["devices"]), 1)
        self.assertTrue(snapshots[2]["inventory_pending"])
        self.assertEqual(sum(topic.startswith("homeassistant/sensor/maria_dashboard_") for topic, payload in published), 5)
        online_config = "homeassistant/binary_sensor/maria_020000000001/online/config"
        self.assertEqual(sum(topic == online_config and bool(payload) for topic, payload in published), 1)
        failed_state_index = next(i for i, (topic, payload) in enumerate(published)
                                  if "/dashboard/" in topic and payload and topic.endswith("/state") and json.loads(payload)["stale"])
        self.assertFalse(any(topic == online_config and not payload for topic, payload in published))


    def test_router_errors_never_expose_login(self):
        error = Exception("invalid user name or password /login password=synthetic-secret")
        self.assertEqual(self.m.safe_router_error(error), "authentication_failed")
        self.assertEqual(self.m.safe_router_error(TimeoutError()), "connection_timeout")
        self.assertEqual(self.m.safe_router_error(Exception("synthetic-secret")), "router_api_error")

    def test_mqtt_reconnect_requests_one_refresh(self):
        import threading
        client, event = Mock(), threading.Event()
        self.m.configure_mqtt_recovery(client, event)
        client.on_connect(client, None, {}, 5, None)
        self.assertFalse(event.is_set())
        client.on_connect(client, None, {}, 0, None)
        self.assertTrue(event.is_set())
        client.publish.assert_called_with(self.m.AVAILABILITY_TOPIC, "online", qos=1, retain=True)
        event.clear()
        client.on_connect(client, None, {}, 0, None)
        self.assertTrue(event.is_set())

    def test_single_ipp_reason_is_not_split_into_letters(self):
        reason = "marker-waste-full-error"
        self.assertEqual(self.m.normalize_reasons(reason), [reason])
        health = d.printer_health(device(printer={"ipp": {"reasons": reason}}))
        self.assertEqual(health["reasons"], [reason])
        self.assertTrue(health["problem"])


if __name__ == "__main__":
    unittest.main()
