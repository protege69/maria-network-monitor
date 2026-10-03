# Maria Network Monitor — configuration

## What it does

- Connects to one or more MikroTik routers through RouterOS API (TCP 8728).
- Publishes only devices represented by static DHCP leases.
- Uses the device MAC address as the stable identifier.
- Determines online state from ping, known TCP services, and MikroTik ARP state.
- Publishes Home Assistant entities through MQTT Discovery.
- Detects printers and collects page counters, supplies, serial/model/status data through IPP and SNMP Printer-MIB.
- Stores known inventory and last-good printer telemetry in `/data`, so app rebuilds/restarts do not wipe runtime state.

## Requirements

1. Home Assistant OS / Supervisor with support for Apps.
2. An MQTT broker reachable from the app. The default host is `core-mosquitto`.
3. RouterOS API access from Home Assistant to every configured router on TCP 8728.
4. A read-only RouterOS account is strongly recommended.
5. Routes/VPNs from Home Assistant to the managed site networks must already work.

## MikroTik account

Create a dedicated read-only user/group that can read:

- system identity
- IP addresses
- DHCP server leases
- ARP table

Restrict RouterOS API access to the Home Assistant source address wherever possible.

## App configuration

Example:

```yaml
mikrotik_username: HA_READ
mikrotik_password: "CHANGE_ME"

mqtt_host: core-mosquitto
mqtt_port: 1883
mqtt_username: maria_monitor
mqtt_password: "CHANGE_ME"

state_interval: 60
discovery_interval: 600
expire_after: 180

printer_ipp_interval: 60
printer_snmp_interval: 600
snmp_community: public

sites:
  - name: Office
    router: 10.10.0.1
    lan: 192.168.10.0/24
  - name: Branch
    router: 10.10.0.2
```

### `sites[].lan`

`lan` is optional. When omitted, the app tries to determine the site's `/24` LAN from RouterOS `/ip/address`, preferring an address on an interface whose name contains `bridge`.

Set `lan` explicitly if the router has several private `/24` networks and automatic detection can choose the wrong one.

## Important behavior

### Managed inventory

Only static DHCP leases are published. Removing a static lease removes the corresponding MQTT Discovery entity on a successful RouterOS poll.

If a site's RouterOS API is temporarily unavailable, existing entities for that site are **not** removed.

### MQTT Discovery

On app startup, discovery is published for the existing inventory. During normal operation, discovery is only published for newly detected managed devices. This avoids periodic Home Assistant `Unavailable → Connected` flapping caused by republishing all discovery configs.

### Printers

IPP is the primary telemetry source. SNMP Printer-MIB is used as fallback and for fields that are unavailable through IPP. The last successful telemetry is cached in `/data/printer_cache.json`.

## Moving to another Home Assistant

Install the app from the same repository on the new Home Assistant and recreate the app options. Normally you do **not** need to copy `/data`.

If you want to preserve the printer cache and known-device state, restore the app data from a Home Assistant backup instead of copying individual files by hand.
