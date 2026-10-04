# Maria Network Monitor

Home Assistant App for monitoring managed devices behind MikroTik routers and publishing them through MQTT Discovery.

The app treats **static DHCP leases** as managed inventory. Dynamic leases are ignored. Device identity is based on MAC address, while IP addresses may change.

Printer telemetry is collected using generic IPP with Printer-MIB/SNMP fallback.

Version 1.4.0 adds a generated site dashboard and compact reactive device/printer cards. Connect the supplied YAML and local frontend resource once using [DASHBOARD.md](DASHBOARD.md); device inventory then updates automatically. User dashboards and existing MQTT identifiers are preserved.

See [DOCS.md](DOCS.md) for installation and configuration.

1.4.0 also adds persistent daily print reports with CSV, visible history cards and outage resilience. Read [REPORTS.md](REPORTS.md) before updating.
