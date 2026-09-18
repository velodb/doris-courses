# Add Data Warehousing Level 1 core course materials

Suggested state: **Draft**. This is a Level-sized initial materials package,
not a single-video PR and not a claim that all planned Level 1 labs are complete.

## Summary

- Add an independent `02-data-warehousing/level1` course, preserving the agreed
  D01/D02/D03/D04/D05/D06/D07 teaching order.
- Add seven course readings mapped to 25 video topics, seven Lab notebooks,
  seven interactive quiz notebooks and 35 questions.
- Add original synthetic order fixtures and independent expected results.
- Implement and validate the core order workflow: load, reject, clean,
  maintain current state, retain history and replay.
- Align numbered reading/quiz filenames, single-node environment layout, and
  notebook presentation with course 01; reuse its quiz and display components.
- Add explicit opt-in Docker preparation with a separate Compose project and volumes.
- Add offline tests, a notebook-cell runner and explicit integration gaps.
- Expand D01 into a learner-facing module: scenario, objectives, complete SQL,
  expected results, troubleshooting and an independent filtering exercise.
- Separate learning entry pages from maintainer validation/status details.
- Keep course 02's root focused on learner materials; move maintenance
  documents, scripts and tests to repository-level maintenance/02-data-warehousing.
- Add a shared Jupyter launch configuration that hides generated artifacts
  without deleting them.
- Align all seven readings with course 01's module structure using Chinese
  headings, standalone lab/summary/quiz sections and official references.

## WWI dataset integration (2026-09-18)

- Replace the D01–D04 historical baseline with an attributed WWI subset; D05 loads
  all 701,846 rows from ten local Parquet files with manifest checks.
- Keep new simulation orders in a separate ID range/source, referencing WWI
  customers and products. Add payment, refund and shipment ledgers for reconciliation.
- Update D06 to 13 inputs / 10 accepted / 3 rejected, and D07 to 11 current
  orders / 18 history events / 19 deliveries. Update readings and relevant quizzes.
- Retain local-only author preparation: no S3 upload, no credentials, no committed
  large files, and no claim that real CDC or Iceberg has been validated.
- Existing learner quiz outputs and course 01 changes remain excluded.

## Validation

See [VALIDATION.md](VALIDATION.md) for build IDs, observed results and limitations.
Core notebook SQL has been executed on a development cluster; the target
4.1.3 release and external integrations are not yet qualified.
All 18 offline tests passed after structure/presentation alignment. The six core
labs passed the cell runner and real Jupyter kernels again; all seven quizzes
emitted widget-view output in fresh kernels. Compose configuration and mocked
startup tests passed; actual Docker startup and browser visual checks remain
unverified. See the validation record for initial and follow-up runs.

The D01 learner-facing revision passes 20 offline tests on an exported index
snapshot, two fresh-kernel D01 runs, its quiz widget check, and another full
six-core-lab cell-runner pass. Learner notebook outputs were left untouched
and excluded from the revision.

After the directory cleanup, 22 offline tests and all six core labs passed
with the relocated tooling. The Jupyter contents API confirms the simplified
course root and access to maintenance links.

The subsequent Chinese structure revision passes 23 offline tests, including
section order and links. Only reading and notebook Markdown changed; all lab
code cells match the prior version, so no additional SQL run was performed.

## Remaining scope

Iceberg environment, file queries, Kafka, CDC, object-storage continuous loads,
Group Commit, larger performance demonstrations and recording remain open.
D04 is a candidate lab requiring an instructor-provisioned external table.
These are not silently skipped or represented by synthetic internal tables.
The other Level 1 modules still need the same teaching depth and guided
steps as the revised D01; filename/UI alignment alone is not completion.

## Dependencies and review focus

Based on merged main, not dependent on PR #2 or #3. Existing display and quiz code is
loaded from this repository, so keep both course directories when installing.
Review the order/event contract, SQL learning sequence, assertions, explicit
reset scope, and separation between implemented and planned material.

No remote PR has been created by this local preparation.
