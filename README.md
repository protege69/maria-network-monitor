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

## 1.4.0 dashboard

One **Maria Network Monitor** dashboard provides a site overview and one responsive view per site. A bundled local resource renders static-lease inventory automatically, including compact printer telemetry and supply warnings. One-time registration is documented in [DASHBOARD.md](maria_network_monitor/DASHBOARD.md). No HACS dependencies or edits to user dashboards are required.

Open `preview.html` in a browser for a standalone synthetic-data preview; its buttons simulate new leases and outages. It uses the production card code and simple HA theme/icon shells, not a live HA server. [ARCHITECTURE.md](ARCHITECTURE.md) explains the collector model and future daily reporting.

Run offline checks with `python -m unittest discover -s tests -v` and `node tests/frontend.test.mjs` (or `node --test tests/frontend.test.mjs` where process spawning is available). Production RouterOS, MQTT, HA history and the Supervisor build still require an on-device smoke test.

## Security

Do not commit real RouterOS passwords, MQTT passwords, SNMP private communities, VPN keys, or `/data` runtime files to this repository.


Version 1.4.0 adds daily print reports, explicit history and outage resilience. See [upgrade and report rules](maria_network_monitor/REPORTS.md).
