"""Demo Live Mode: optional, demo-database-only background activity that drives the REAL application API.

Nothing here writes business tables directly. Orders, payments, receipts, production steps and quality readings are created by calling the same
HTTP endpoints as a person would (so validation, permissions, approvals, ledger postings, audit rows, notifications and outbox events all happen),
as the seeded `demo.*` users. The only tables owned by this package are the control row, the event log and one-shot triggers (migration 0027).
Simulated readings (instrument telemetry) are produced by the event stream, labelled Simulated, and stored nowhere."""
