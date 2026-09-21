# Module 10: Managing data

## Goal

Operate on an isolated Doris table with explicit lifecycle boundaries and
observable metadata. The module focuses on safe partition maintenance rather
than destructive actions against the shared course baseline.

## Learning outcomes

By the end of this module, learners can:

- read `SHOW CREATE TABLE`, `SHOW PARTITIONS`, and `SHOW TABLETS` as management
evidence;
- add a time-range partition without rewriting existing partitions;
- choose partition-level truncation when the retention boundary is a partition;
- distinguish logical visibility from background compaction and physical
reclamation;
- write a runbook that names the table and partition boundary before a
destructive operation.

## Lab

[`lab10_manage_data.ipynb`](lab10_manage_data.ipynb) owns isolated management
and scratch tables. It creates a partitioned table, inspects its physical
layout, adds a lifecycle partition, truncates only one partition, and verifies
that other partitions remain available.

> Do not run management statements against `doris_course.events` or another
> module's table. The shared Level 1 baseline is read-only for this module.
