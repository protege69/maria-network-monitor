# Maria Network Monitor 1.3.0 — architecture

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

`maria_monitor.py` keeps existing device identities/topics, transport, schedules and cache. `dashboard.py` adds a versioned inventory model, site summary sensors, fault interpretation and owned-file export. The frontend renders inventory from HA states and resolves actual MQTT entity IDs through the HA entity registry only when opening history. It never derives entity IDs from DHCP comments or assumes a user has not renamed an entity.

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

## Statistics and future reports

The current release exposes lifetime printer counters and time series of online/offline/problem counts. Existing page-counter sensors retain `total_increasing`, supporting HA Recorder statistics where the source is a valid lifetime counter. It does **not** label a lifetime reading as pages printed today or create daily reports yet.

A future reporting layer should use a metric contract: device/site ID, metric name, source, unit, successful observation time, quality and lifetime/reset semantics. Additional RouterOS resource/interface metrics can then be collected without changing the GUI/inventory contract.

Daily page reports must compute increments from valid fresh observations, retain a restart-safe baseline, detect counter resets/device replacement and choose a configured reporting timezone. Missing polls spanning midnight cannot establish the exact day of printing: reports must flag incomplete coverage rather than distribute pages with false precision. Distinguish calendar-day totals, previous calendar day and rolling 24 hours. SNMP lifetime page counters and IPP job/session counters must not be treated as interchangeable without validating the printer's semantics. Preserve source changes explicitly.

For a manually configured prototype HA Utility Meter can accumulate a valid counter by calendar cycle. For automatic reporting across arbitrary discovered printers, add a collector-owned aggregation/export layer or an HA integration that manages those entities; do not automatically rewrite HA configuration for every printer. CSV and management summaries can consume the same aggregates later.

Inventory attributes are current operational data and can be large. Exclude the inventory entities from Recorder after finding their real entity IDs, while retaining the numeric summary and page-counter sensors. For very large sites, split inventory into metadata plus per-device snapshots in a later schema; there is no universal fleet-size limit established by these offline tests.

## Validation scope

Synthetic regression tests cover legacy identities, static-only inventory, cache survival, delayed printer discovery, idempotent export, foreign-file protection, stale sites, count semantics, warning boundaries and new devices without YAML edits. A standalone preview exercises the exact frontend module with synthetic HA states, desktop/mobile layout and simulated new leases/outages. It is not a live Supervisor/Docker/MQTT installation test; an on-device smoke test remains necessary before production rollout.
