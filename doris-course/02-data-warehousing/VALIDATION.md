# Validation record

Date: 2026-09-17. Status: first Level 1 draft, not release-qualified.

## Database execution

Six core notebooks were executed cell by cell through `scripts/run_labs.py`
against an existing, single-node integrated development cluster. The runner
executes the committed Python cells, not an alternate SQL implementation.
The complete core sequence passed twice against the same dedicated database,
including table reset and reinitialization on the second run.
All six core notebooks also passed a third run using real Jupyter kernels
through nbclient, starting each notebook in its own module directory.

- FE: `doris-0.0.0-ad8644154c3`.
- BE: `doris-0.0.0-8bafeb1e4c4`.
- The two components are development builds at different commits.
- Target recording release remains 4.1.3; these results do **not** qualify it.
- A dedicated `dw_course_l1_` database was used. No existing course/business
  tables were reset, and no cluster processes or global settings were changed.

Observed assertions:

| Lab | Evidence |
|---|---|
| D01 | Ten initial orders; order amount 1400.00 |
| D02 | Identical row totals for bulk and individual writes; Tablet metadata inspected |
| D03 | Duplicate/Unique/Aggregate row semantics; partition and bucket plan output |
| D05 | Ten-row Stream Load success; duplicate label rejection without added rows; bad batch rejected |
| D09-A | 12 raw, 10 valid, 2 rejected; full-row comparison; injected duplicate detected and repaired |
| D06 | 11 current orders, 16 historical events; out-of-order and repeat delivery; step-interruption recovery; partial update; isolated soft/SQL deletion |

D06 records one initial interrupted delivery and two seven-delivery attempts,
so the raw delivery table contains 15 rows. Business current/history results
remain unchanged. Paid GMV is 250.00 and refund amount 150.00.

## Validation methods and exclusions

Run offline checks with `python -m unittest discover -s tests -v`.
All 11 tests passed in the authoring environment.
They validate fixtures against an independent replay calculation, clean notebook
structure and Python syntax, quiz definitions and shared renderer loading,
relative Markdown links, explicit write opt-in and scoped database names,
assertion failure, model DDL generation, and rejection of HTTP redirects.

Notebook outputs remain empty in git. Browser rendering, recorded videos,
Doris 4.1.3, Iceberg and the paths listed in
[integration-backlog.md](integration-backlog.md) are **not verified**.
The small correctness fixtures do not establish performance or resilience.
