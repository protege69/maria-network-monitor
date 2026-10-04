# Validation — 1.3.0 candidate

- 22 Python offline tests passed, including a simulated three-cycle main loop: success → RouterOS failure → confirmed lease removal. Failed polling retained inventory; only confirmed removal deleted the legacy discovery entity. Summary discovery was published once per entity during the run.
- 9 JavaScript tests passed: live device addition/removal, repeated-update deduplication, monitor unavailability, stale RouterOS data, grouping/order, literal untrusted names and renamed MQTT entity history resolution.
- Python compilation and JavaScript syntax checks passed.
- The standalone browser preview uses the shipped frontend code with synthetic inventory and HA theme/icon shells. New-lease injection visibly added a PC without reload/YAML changes. RouterOS outage visibly preserved devices and replaced current counts/connections with unknown-data labels.
- At a mobile viewport of 390 × 844, measured card width was 343 px, the printer grid used one 305 px column, and document width did not exceed the viewport. Desktop uses multiple columns and compact supply bars.
- Runtime data, real site names, addresses and credentials are not included in the package. Documentation/test examples use documentation-only IP ranges and fictitious inventory.

Not executed: Docker image build (no Docker executable available), installation on Supervisor, real RouterOS/SIP/SNMP/IPP polling, end-to-end MQTT delivery, HA YAML resource loading and Recorder history. The user's running HA configuration was not changed.

## On-device smoke test before rollout

1. Back up HA/app data, install this candidate and complete the one-time resource/dashboard connection in DASHBOARD.md.
2. Confirm legacy entities retain their identities, cached printer values survive restart and Maria adds exactly one dashboard.
3. Add a synthetic static lease on a test router; verify a new card and discovery entity. A dynamic lease must not appear.
4. Interrupt RouterOS access; verify stale inventory without entity deletions or false zero counts. Restore access and confirm recovery.
5. Check a real printer's lifetime counter and supplies against its own interface, especially waste toner and drum semantics.
6. Verify history access with intended user roles and Recorder configuration, then review desktop/mobile layout inside HA.

Automatic daily print reports are future work. This release's lifetime page counter must not be reported as a daily total.
