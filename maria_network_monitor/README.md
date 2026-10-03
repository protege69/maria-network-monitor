# Maria Network Monitor

Home Assistant App for monitoring managed devices behind MikroTik routers and publishing them through MQTT Discovery.

The app treats **static DHCP leases** as managed inventory. Dynamic leases are ignored. Device identity is based on MAC address, while IP addresses may change.

Printer telemetry is collected using generic IPP with Printer-MIB/SNMP fallback.

See [DOCS.md](DOCS.md) for installation and configuration.
