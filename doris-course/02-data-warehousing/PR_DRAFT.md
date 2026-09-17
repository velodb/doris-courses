# Add Data Warehousing Level 1 core course materials

Suggested state: **Draft**. This is a Level-sized initial materials package,
not a single-video PR and not a claim that all planned Level 1 labs are complete.

## Summary

- Add an independent `02-data-warehousing/level1` course, preserving the agreed
  D01/D02/D03/D04/D05/D09-A/D06 teaching order.
- Add seven course readings mapped to 25 video topics, seven Lab notebooks,
  seven interactive quiz notebooks and 35 questions.
- Add original synthetic order fixtures and independent expected results.
- Implement and validate the core order workflow: load, reject, clean,
  maintain current state, retain history and replay.
- Reuse the existing quiz renderer without modifying the real-time analytics course.
- Add offline tests, a notebook-cell runner and explicit integration gaps.

## Validation

See [VALIDATION.md](VALIDATION.md) for build IDs, observed results and limitations.
Core notebook SQL has been executed on a development cluster; the target
4.1.3 release and external integrations are not yet qualified.
All 11 offline tests passed. The six core labs passed two cell-runner passes
and one Jupyter-kernel pass, including reruns against the same course database.

## Remaining scope

Iceberg environment, file queries, Kafka, CDC, object-storage continuous loads,
Group Commit, larger performance demonstrations and recording remain open.
D04 is a candidate lab requiring an instructor-provisioned external table.
These are not silently skipped or represented by synthetic internal tables.

## Dependencies and review focus

Based on merged main, not dependent on PR #2 or #3. The existing quiz renderer is
loaded from this repository, so keep both course directories when installing.
Review the order/event contract, SQL learning sequence, assertions, explicit
reset scope, and separation between implemented and planned material.

No remote PR has been created by this local preparation.
