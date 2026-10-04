# Validation — 1.4.0

- 36 Python tests passed: legacy IDs/cache/static filtering, no routine discovery flapping, export ownership/idempotency, fault semantics, safe errors, MQTT recovery event, daily duplicate/restart/midnight/reset/source/timezone/retention cases, deletion confirmation, and slow-site isolation.
- 14 JavaScript tests passed: reactive inventory, stale snapshots, report unknown versus measured values, native history entity resolution/error state, CSV safety, renamed IDs and untrusted names.
- Python compilation and JavaScript syntax checks passed.
- Production frontend was rendered with clearly synthetic fixtures. Desktop report, date selection including a no-data day, and mobile report were checked in the browser. At 390×844, document width was 390 and report card width was 358: no horizontal overflow.
- Archive excludes Python bytecode/runtime data. A targeted scan found no production site names, private HA host or credentials. Example addresses are documentation-only.

Not executed: Docker build (Docker unavailable), Supervisor install, real printer polling, live HA native-history card loading, and real MQTT broker restart. Live deployment must be verified separately. Native card creation/registry resolution were tested with mocks, not passed off as a live HA graph test.

Deployment smoke test: backup; install 1.4.0; set/verify report timezone; update the existing JS resource URL; refresh dashboard; verify retained legacy entities, the new report view, native history, and two genuine counter samples. Test connectivity interruption and broker reconnect in a maintenance window. See REPORTS.md.
