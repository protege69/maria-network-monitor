# Maria Network Monitor 1.4.0 — architecture

Maria is a network telemetry collector. Home Assistant is its first presentation and history consumer; dashboards do not own the monitoring or reporting rules.

## Data flow

```text
MikroTik static DHCP + ARP → ping/TCP → IPP/SNMP + last-good cache
        ↓                              ↓
existing MQTT Discovery entities       normalized inventory + health policy
        ↓                              ↓
Home Assistant history                additive MQTT site summaries / inventory
                                       ↓
                              one YAML dashboard + reactive local cards
```

`maria_monitor.py` keeps existing device identities/topics and cache; router scheduling now uses bounded waits and retry backoff. `dashboard.py` adds a versioned inventory model, site summary sensors, fault interpretation and owned-file export. The frontend renders inventory from HA states and resolves actual MQTT entity IDs through the HA entity registry only when opening history. It never derives entity IDs from DHCP comments or assumes a user has not renamed an entity.

The reference dashboard was a layout example from another organization, not production configuration. Its site names, device names, entity IDs and addresses are not copied into this repository.

## GUI

One sidebar dashboard named **Maria Network Monitor**. Overview contains all configured sites; each site has a Sections view with summary, printers, computers, cameras, access points, servers, VoIP and unknown devices. Printer cards show connectivity, lifetime page count, status, queue, acceptance, supplies and last-success timestamps. Percentages use slim horizontal bars. Offline devices have a red edge and explicit label. Warnings use an amber edge and text, independently of color vision. Theme colors follow HA; grids collapse on phones.

Only the layout is YAML. Device lists are reactive cards receiving each HA state update. Adding/removing a static lease does not modify dashboard configuration or require a page reload. Adding/changing the `sites` option rebuilds views at app startup; HA may require the documented dashboard Refresh action to load new views. This is deliberately different from claiming live regeneration of the view list.

No HACS package or remote JavaScript is required. The single module is supplied with this app. This is a custom frontend resource, not a promise that native HA cards alone can dynamically generate arbitrarily many printer cards.

## Compatibility and correctness

- MAC device IDs, existing MQTT topics and printer sensor IDs are unchanged.
- `known.json` retains its existing format. `dashboard_inventory.json` is separate.
- IPP/SNMP cache records acquire optional successful-observation timestamps; old cache files remain readable.
- Last-good telemetry is retained after failure. Its freshness is shown separately.
- Existing discovery is not republished each polling interval. A small printer publisher forwards only new entity topics, including telemetry discovered after initial startup. Missing telemetry never deletes a cached printer metric. Explicit lease removal still uses the existing deletion path.
- Failed RouterOS polling preserves the site's inventory. Live summary counts are unavailable, not zero. Device tiles show unknown freshness rather than claiming a current online/offline result.
- Types persist across offline polls and restarts; confidently detected types can replace older types.
- Each printer contributes at most one to the problematic-printer count, even with several faults.
- Toner at 20% or below warns. Unknown/negative levels do not become 0%. Drum/OPC are displayed without inventing model-specific thresholds. Waste toner never receives a low-percentage warning; explicit IPP full/error reasons still count.
- Site view IDs use a readable prefix plus a hash, including for Unicode names. Legacy router IDs still use v1.2's slug rule: colliding names are rejected rather than silently merging MQTT router entities. A single existing Unicode-only site remains compatible.

## Idempotency and ownership

The generator uses stable ordering, a fixed output filename and an ownership header. Unchanged content does not rewrite files. Changed content is written to a temporary sibling then atomically replaced. Foreign destination files and symbolic links are rejected. Only the app's generated YAML and JavaScript are written; HA `configuration.yaml`, `.storage`, dashboard registration and user dashboards are never edited.

The fallback is a ready YAML file and module in `/share/maria_network_monitor` and `/data/dashboard`. The shared copy is easy to retrieve through Samba or a file editor supporting `/share`. Once the YAML dashboard and resource are connected, normal device updates are automatic. The registration itself is a documented one-time setup because no documented public REST dashboard-management endpoint was found.

## Statistics and daily reports

Lifetime printer counter entities retain `total_increasing` and their original IDs. `reports.py` separately computes daily deltas from source-tagged, timestamped observations and persists a bounded 90-day journal. The frontend offers per-site and fleet reports, date selection and CSV export. Reset, source-change, incomplete-day and no-sample flags are part of the data contract, not visual guesses. Cross-midnight deltas remain explicitly unallocated.

Detailed state history comes from HA Recorder through a native history card. Diagnostic inventory/report attributes can be excluded from Recorder while retaining numeric counters and connectivity states. Future RouterOS resource/interface metrics can use the same observation/source/quality contract.

## Validation scope

Synthetic regression tests cover legacy identities, static-only inventory, cache survival, delayed printer discovery, idempotent export, foreign-file protection, stale sites, count semantics, warning boundaries and new devices without YAML edits. A standalone preview exercises the exact frontend module with synthetic HA states, desktop/mobile layout and simulated new leases/outages. It is not a live Supervisor/Docker/MQTT installation test; an on-device smoke test remains necessary before production rollout.


## Reliability and persistence in 1.4.0

`reports.py` maintains a versioned, atomic `/data/daily_reports.json` ledger. Samples identify their source and observation timestamp. Each site/printer has a baseline and bounded daily buckets; duplicate or out-of-order samples do not affect totals. Midnight-crossing deltas are kept separately, resets/source changes invalidate comparisons, and missing data stays unknown. Timezone is explicit or inherited from TZ; changing an existing ledger timezone requires a deliberate migration, never silent relabeling.

`resilience.py` confirms lease absence before removal and holds at most one outstanding router poll per site. A shared worker pool has a bounded collection wait and exponential retry backoff. This is a cycle waiting budget, not a process hard-kill deadline. Snapshot entities intentionally do not expire or inherit MQTT availability: the UI computes freshness from their observation timestamp; numeric live sensors retain availability/expiry.

The frontend includes a daily report view and a per-site native HA history graph. Reports are independent of Recorder retention; native detailed state history still depends on Recorder. The MQTT queue is bounded, and rejected config publication schedules recovery. Config messages are sent on initial/recovery/new-metric events, not unconditionally each cycle.
