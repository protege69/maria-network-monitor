"""Persistent daily counter deltas. Never allocate a cross-midnight delta by guess."""
import math
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from dashboard import site_key

RETENTION_DAYS = 90


def sample(device):
    data = device.get("printer") or {}
    source = data.get("page_count_source")
    stamp = data.get("page_count_observed_at")
    count = data.get("page_count")
    if (not source or not isinstance(stamp, (int, float)) or not math.isfinite(stamp)
            or not isinstance(count, (int, float)) or isinstance(count, bool)
            or not math.isfinite(count) or count < 0 or int(count) != count):
        return None
    return {"source": source, "at": stamp, "count": int(count)}


def update_reports(state, devices, timezone_name, now=None):
    """Mutate a private, persisted ledger. Duplicate/cached samples are no-ops."""
    now = now if now is not None else datetime.now(timezone.utc).timestamp()
    zone = ZoneInfo(timezone_name)
    today = datetime.fromtimestamp(now, zone).date()
    cutoff = (today - timedelta(days=RETENTION_DAYS - 1)).isoformat()
    if state and state.get("timezone") != timezone_name:
        raise ValueError("Report timezone changed; preserve/archive the old ledger before migration")
    state.update(schema=1, timezone=timezone_name)
    printers = state.setdefault("printers", {})
    for device in devices:
        if device.get("type") != "printer":
            continue
        key = site_key(device["site"]) + ":" + device["id"]
        record = printers.setdefault(key, {"id": device["id"], "site_id": site_key(device["site"]),
                                            "first_day": today.isoformat(), "days": {}})
        record["name"] = device["name"]
        record["last_seen"] = now
        current = sample(device)
        if current is None or current["at"] > now + 60:
            continue
        previous = record.get("last")
        if previous and current["at"] <= previous["at"]:
            continue
        day = datetime.fromtimestamp(current["at"], zone).date().isoformat()
        if day < cutoff:
            continue
        bucket = record["days"].setdefault(day, {"pages": 0, "samples": 0, "flags": [], "unallocated": 0})
        bucket["samples"] += 1
        bucket["last_at"] = current["at"]
        if previous is None:
            bucket["flags"].append("partial_start")
        elif previous["source"] != current["source"]:
            bucket["flags"].append("source_changed")
        elif current["count"] < previous["count"]:
            bucket["flags"].append("counter_reset")
        else:
            delta = current["count"] - previous["count"]
            prior_day = datetime.fromtimestamp(previous["at"], zone).date().isoformat()
            if prior_day == day:
                bucket["pages"] += delta
            elif delta:
                bucket["unallocated"] += delta
                bucket["flags"].append("boundary_gap")
                bucket["gap_from"] = previous["at"]
                bucket["gap_to"] = current["at"]
                old = record["days"].get(prior_day)
                if old:
                    old["flags"] = sorted(set(old["flags"] + ["boundary_gap"]))
        bucket["flags"] = sorted(set(bucket["flags"]))
        record["last"] = current
    for key, record in list(printers.items()):
        record["days"] = {day: value for day, value in record["days"].items() if day >= cutoff}
        if now - record.get("last_seen", now) > RETENTION_DAYS * 86400:
            del printers[key]
    return state


def site_report(state, site, now=None, stale_after=1800):
    now = now if now is not None else datetime.now(timezone.utc).timestamp()
    zone = ZoneInfo(state["timezone"])
    today = datetime.fromtimestamp(now, zone).date()
    records = [r for r in state.get("printers", {}).values() if r["site_id"] == site["site_id"]]
    days = []
    for offset in range(RETENTION_DAYS):
        day = (today - timedelta(days=offset)).isoformat()
        rows = []
        for record in records:
            if day < record["first_day"]:
                continue
            bucket = record["days"].get(day, {})
            flags = list(bucket.get("flags", []))
            if not bucket.get("samples"):
                flags.append("no_samples")
            if offset > 0:
                end = datetime.combine(datetime.fromisoformat(day).date() + timedelta(days=1),
                                       datetime.min.time(), zone).timestamp()
                if record.get("last", {}).get("at", 0) < end:
                    flags.append("partial_end")
            if offset == 0 and now - bucket.get("last_at", 0) > stale_after:
                flags.append("stale")
            rows.append({"id": record["id"], "name": record["name"],
                         "pages": bucket.get("pages") if bucket.get("samples") else None,
                         "flags": sorted(set(flags)), "last_at": bucket.get("last_at"),
                         "unallocated": bucket.get("unallocated", 0), "gap_from": bucket.get("gap_from"),
                         "gap_to": bucket.get("gap_to")})
        if rows:
            days.append({"date": day, "rows": rows, "pages": sum(r["pages"] or 0 for r in rows),
                         "has_samples": any(r["pages"] is not None for r in rows),
                         "in_progress": offset == 0, "incomplete": any(r["flags"] for r in rows)})
    return {"schema": 1, "maria_role": "daily_report", "site_id": site["site_id"],
            "timezone": state["timezone"], "generated_at": now, "today": today.isoformat(), "days": days,
            "persistence_ok": state.get("persistence_ok", True)}


def report_discovery(site):
    uid = "maria_dashboard_" + site["site_id"] + "_report"
    topic = "maria-monitor/reports/" + site["site_id"] + "/state"
    return "homeassistant/sensor/" + uid + "/config", {
        "unique_id": uid, "name": "Daily print report", "state_topic": topic,
        "value_template": "{{ value_json.today }}", "json_attributes_topic": topic,
        "entity_category": "diagnostic", "icon": "mdi:file-chart",
        "device": {"identifiers": ["maria_dashboard_" + site["site_id"]]}}
