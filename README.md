# Maria Network Monitor — Home Assistant App repository

This repository contains the **Maria Network Monitor** Home Assistant App.

It builds a managed network inventory from MikroTik static DHCP leases, publishes devices through MQTT Discovery, and adds generic printer telemetry through IPP/SNMP.

## Install in Home Assistant

1. Open the Home Assistant App store.
2. Open the repository management dialog.
3. Add the URL of this Git repository.
4. Install **Maria Network Monitor**.
5. Configure RouterOS API, MQTT credentials, and the `sites` list before starting the app.

See [`maria_network_monitor/DOCS.md`](maria_network_monitor/DOCS.md) for configuration details.

## Security

Do not commit real RouterOS passwords, MQTT passwords, SNMP private communities, VPN keys, or `/data` runtime files to this repository.
