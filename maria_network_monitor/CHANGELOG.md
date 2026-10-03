# Changelog

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
