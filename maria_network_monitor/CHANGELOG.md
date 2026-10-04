# Changelog

## 1.4.0

- Added persistent daily print reports by site/device, a dashboard date selector, seven-day bars, 90-day retention and CSV export. Counts are deltas from actual sample timestamps; cached samples never count twice.
- Added explicit data-quality flags for first observation, counter reset, source changes, missing samples and incomplete days. Cross-midnight deltas are recorded separately, without invented daily allocation.
- Added visible History cards using native Home Assistant history graphs, current registry entity IDs and links to more-info for other dates.
- Keep retained inventory visible during monitor outages; frontend freshness expires independently every 30 seconds.
- Confirm missing leases over three successful polls; failures reset the absence streak and preserve devices.
- Bound router collection waiting to 20 seconds per cycle, reuse outstanding jobs and back off failing routers up to five minutes. Set RouterOS socket timeout to ten seconds.
- Bound MQTT queued messages to 256, retry Discovery after queue rejection, recover on HA birth, and retain per-site rediscovery until routers recover.
- Preserve legacy entity IDs, printer cache, user dashboards and all 1.3.1 fixes.


## 1.3.1

- Restore online availability and republish Discovery after successful MQTT reconnect, using a main-thread event; stable IDs and normal no-flapping behavior are preserved.
- Replace RouterOS exception text with safe error categories so login commands cannot leak into logs or MQTT.
- Normalize single IPP reason strings before caching, MQTT formatting and dashboard health evaluation.
- Report total leases, static leases and exclusion counts instead of presenting filtered count as the router's entire static inventory.
- Document remaining outage/history limitations and deployment verification in REVIEW.md.


## 1.3.0

- Explicitly group SIP/SIPS phones under VoIP, using name/hostname hints and TCP 5060/5061 where available; PBX registration status is not inferred from reachability.

- Added one generated Maria Network Monitor YAML dashboard, an overview of all sites, and a Sections view per site.
- Added a local reactive frontend module: new static leases update visible device lists without manual card creation or dashboard refresh.
- Added persistent dashboard inventory, stable view IDs, offline type persistence, site summary MQTT sensors, and stale-data handling after RouterOS/API or monitor failures.
- Added compact printer cards with lifetime page counters, status/queue/acceptance, supply bars, IPP/SNMP timestamps, and SNMP-only drum/waste supplementation in the UI model.
- Toner at or below 20% warns; waste toner is never interpreted as bad merely because its percentage is low. Each faulty printer is counted once.
- Preserved existing MQTT device IDs, discovery topics, cache and no-periodic-discovery-flapping behavior. Printer metrics arriving after initial discovery now publish only new entity topics. Missing telemetry no longer causes printer marker tombstones; removing a managed lease still removes its entities.
- Added owned-file, atomic and idempotent export into the HA config mount, shared fallback and app data. No writes to user dashboards, configuration.yaml or .storage.
- Added one-time dashboard/resource connection instructions, architecture/reporting notes, synthetic regression tests and a standalone desktop/mobile preview.
- Clarified that lifetime counters are not daily printing totals; automatic daily reports remain future work.

## 1.2.0

- Prepared the app for installation from a Home Assistant App repository.
- Removed the built-in private site inventory from default options.
- Added optional per-site `lan` override for routers with multiple private networks.
- Removed an environment-specific LAN-detection exception.
- Added an explicit error when no sites are configured.
- Marked the app as stable.
- Preserved the monitoring behavior from production 1.1.5, including printer cache and no-discovery-flapping logic.

## 1.1.5

- MQTT Discovery is no longer republished for every existing device on each discovery interval.
- Discovery is refreshed for all devices at startup and for newly discovered devices during runtime.

## 1.1.4

- Increased device/printer discovery expiry grace to reduce mass `Unavailable` states after a temporary router/API outage.

## 1.1.3

- Added persistent last-good printer telemetry cache in `/data/printer_cache.json`.

## 1.1.2

- Added/fixed SNMP Printer-MIB fallback for printer telemetry when IPP is incomplete or unavailable.

## 1.1.1

- Fixed an MQTT template formatting issue.
