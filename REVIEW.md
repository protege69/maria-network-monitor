# Review — 1.4.0

Implemented: safe RouterOS errors; MQTT reconnect/HA birth/queue-overflow recovery; whole-string IPP reasons; DHCP filter counts; retained last-known inventory with independent freshness; three-poll removal confirmation; bounded router wait with per-site backoff; explicit HA History cards; source-aware daily reports and CSV.

Compatibility: legacy IDs, printer cache, no routine Discovery flapping, owned YAML export and user dashboards are preserved. Earlier releases remain available for rollback.

Remaining limits: incorrect automatic LAN selection still needs diagnosis or an explicit LAN setting; repeated successful filtered-empty polls can confirm removal. Detailed history requires HA Recorder. Daily reports start with the new ledger and cannot reconstruct earlier days automatically. Printing across unobserved midnight boundaries is explicitly unallocated. Host RAM exhaustion and inaccessible HA prevent live operation and require host/network recovery.

This artifact has been tested locally; deployment verification on Supervisor remains necessary. See [deployment and report semantics](maria_network_monitor/REPORTS.md) and [validation](VALIDATION.md).
