# Changelog

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
