# Module 2: Doris Storage Architecture and Write Mechanisms

| Course Information | Details |
| --- | --- |
| Course | Data Warehousing with Apache Doris · Level 1 |
| Product Version | Apache Doris 4.x |
| Lab Version | Apache Doris 4.1.3 |
| Estimated Time | About 50 minutes, including reading, the hands-on lab, and the quiz |

[Course contents](../README.md) · [Open Lab 2](lab02_observe_storage.ipynb) · [Open Quiz 2](quiz02_storage_and_write_batches.ipynb)

## Module Goal

This module introduces the Doris query path, storage structure, and the relationship between different write batches and background compaction.

After completing this module, you will be able to compare batch and row-by-row writes and observe storage metadata such as Tablet information.

## Learning Objectives

After completing this module, you should be able to:

1. Trace a query to explain FE planning and BE execution responsibilities.
2. Explain the hierarchy of Table, Partition, Tablet, Rowset, and Segment.
3. Explain the relationship between write batches, data visibility, and background Compaction.
4. Keep table structure and data constant while comparing batch and row-by-row writes.
5. Distinguish what query plans, storage metadata, and runtime evidence can each tell you.

## Module Schedule

| Section | Learning Format | Suggested Time | Learning Outcome |
| --- | --- | --- | --- |
| 2.1 Columnar Storage and the Query Path | Query flow diagram | 5 minutes | Trace an order query to explain FE, BE, and columnar reads |
| 2.2 Tablet, Rowset, Segment, and Compaction | Hierarchy diagram and examples | 8 minutes | Distinguish shards, write versions, and columnar files |
| 2.3 Write Batches and Visibility | Comparative analysis and status inspection | 12 minutes | Explain why different transaction counts produce the same business results |
| Lab 2 | Hands-on practice | 20 minutes | Write the orders both ways, verify ten rows and 12220.60, and read versions, rowsets, and scan counters |
| Quiz 2 | Interactive quiz | 5 minutes | Check the query path, storage hierarchy, and observation methods |

## 2.1 Columnar Storage and the Query Path

### Trace a Query to Understand Component Responsibilities

Suppose an analyst wants only the amount for order 1, rather than the entire order table. After completing Lab 2,
you can run the following read-only example directly in the same lab database:

```sql
SELECT order_id, order_amount
FROM orders_batch
WHERE order_id = 1;
```

Expect one row: order 1, with a pre-tax amount of 2300.00. The client sees one SQL statement,
but internally the system must parse, plan, read, and compute.

```text
Notebook / SQL client
        │ SQL
        ▼
FE: Resolve fields → Optimize query → Generate and distribute execution plan
        │ Plan fragments
        ▼
BE: Scan required columns → Filter order ID → Return amount
        │
        ▼
Client: Display results
```

FE decides what work to perform, and BE carries out its assigned work. Large queries may also scan and aggregate
across several BEs before combining results. The single-node lab helps explain responsibilities but does not demonstrate multi-node scaling.

### Why Does Analytics Usually Read Only Some Columns?

The order table also contains the customer, date, line count, and source, but this query needs only the order ID and amount.
Columnar storage organizes values from the same column together, making it easier to read only the columns a query needs.
Column pruning answers "which columns to read," while filtering and data skipping answer "which rows or data blocks to process." They are different.

```sql
EXPLAIN SELECT order_id, order_amount
FROM orders_batch
WHERE order_id = 1;
```

Find the scanned table, output columns, and filter conditions in the plan. EXPLAIN shows a plan; it does not mean the query has run,
nor does it provide the actual number of bytes read by this query. Use Query Profile to understand runtime work.

## 2.2 Tablet, Rowset, Segment, and Compaction

When an order reaches Doris, partitioning and bucketing rules first locate the shard responsible for storing it, and then it is written to columnar files.
Follow this process to understand the storage terms: Tablet answers "which shard," Rowset answers
"which data set this write batch created," and Segment answers "which files ultimately hold the data."

### First, Understand Each Layer's Role

```text
Table: A table in SQL
└── Partition: Organizes data by rules such as ranges
    └── Bucket / Tablet: The data shard corresponding to the bucketing rules
        └── Rowset: A versioned data set created by a write or compaction
            └── Segment: An immutable columnar data file
```

| Layer | Understanding it through the order table | Do not confuse it with |
| --- | --- | --- |
| Partition | Divides data ranges by order date; a default partition exists even without explicit partitioning | Not a BE node |
| Bucket / Tablet | Bucketing divides data within a partition into shards; a Tablet is the corresponding physical shard | Bucket count is not replica count |
| Rowset | A write involving a Tablet creates a versioned data set within that Tablet | Not a single file shared by the entire table |
| Segment | A file that stores column data in a Rowset; a Rowset can have multiple Segments | Not a business order |

For example, one partition for each of two days, with four buckets per partition, gives eight Tablets;
configuring replicas adds physical copies, not additional logical orders.
The course sandbox uses a single replica to make it easier to observe writes to one copy of the data.

For the lab's single-bucket order table, submitting ten orders at once writes all of them to the same Tablet;
submitting them in ten separate requests sends ten successive batches to that same Tablet. Each write batch creates its own Rowset,
whose column data is stored in Segments. A large batch can generate multiple Segments,
so business row counts, submission counts, and file counts must be understood separately.

### Why Compact After Writing?

Continuous small-batch writes create multiple data fragments. Reading the same Tablet requires processing these fragments;
Compaction merges multiple Rowsets in the background, reducing the number of fragments that reads must process.
**Compaction changes physical organization and should not change logical query results.**

```text
Within one Tablet:
Write A → Rowset A ┐
Write B → Rowset B ├─ Compaction → Compacted Rowset
Write C → Rowset C ┘
```

Once a write transaction is published as a visible version, queries can read the new data. Background compaction organizes the files afterward:
even if the three batches remain in three separate Rowsets, a query can still return the complete order summary.
Thus, "when business results become visible" and "when files are compacted" are two separate questions.
This is also why write troubleshooting starts with transaction results before checking Compaction.
See [Compaction principles](https://doris.apache.org/docs/4.x/admin-manual/trouble-shooting/compaction-principles/) for the storage-layer compaction process.

## 2.3 Write Batches and Visibility

### Fairly Compare One Ten-Row Write with Ten Separate Writes

The lab uses the same WWI historical orders as Module 1, without changing amounts or dates.

| Comparison item | orders_batch | orders_rowwise |
| --- | --- | --- |
| Data and fields | The same ten orders | The same ten orders |
| Buckets and replicas | One bucket, one replica | One bucket, one replica |
| Write method | One batch of ten rows | One row at a time, ten times |
| Group Commit | off_mode | off_mode |
| Final business result | Ten rows, 12220.60 | Ten rows, 12220.60 |

Group Commit is disabled so that server-side batching does not mask differences between the write methods.
This is not a recommended setting for every production load.

After completing both write methods, check business results before inspecting metadata:

```sql
SELECT 'batch' AS write_mode, COUNT(*) AS orders, SUM(order_amount) AS amount
FROM orders_batch
UNION ALL
SELECT 'small' AS write_mode, COUNT(*) AS orders, SUM(order_amount) AS amount
FROM orders_rowwise
ORDER BY write_mode;
```

```sql
SHOW PARTITIONS FROM orders_batch;
SHOW TABLETS FROM orders_batch;
SHOW TABLETS FROM orders_rowwise;
```

### From Tablet Information to Rowsets

First run SHOW TABLETS above and find each table's TabletId, Version, and VersionCount:

| Field | How to read it | What it does not mean |
| --- | --- | --- |
| TabletId | Use it to locate this shard for further inspection | Not a business order ID |
| Version | The data version position reported by this replica | Not the current file count; Compaction does not reset it to 1 |
| VersionCount | The number of versions reported at sampling time, related to compaction state | Not the submission count or Segment file count |

For example, if one sample shows a higher VersionCount for the row-by-row table than for the batch table, investigate whether it retains more
uncompacted Rowsets. This number alone cannot tell you how much slower a query is. Metadata reporting and background compaction have their own timing;
record the sampling time, and do not expect identical counts on every run.

A tablet's rowsets are listed by the BE that stores it. `SHOW TABLET <TabletId>` returns a `DetailCmd`, a `SHOW PROC` statement that lists the tablet's replicas, and each replica has a `CompactionStatus` URL on its BE. Lab 2 follows this path and reads the URL for both tables:

| Table | Rowsets right after the writes | After cumulative compaction |
| --- | --- | --- |
| orders_batch | `[0-1]` (empty start) and `[2-2]` (the ten rows) | Unchanged: one load rowset has nothing to merge |
| orders_rowwise | `[0-1]` and `[2-2]` … `[11-11]`, one per write | `[0-1]` and `[2-11]`, with the ten old rowsets listed as stale until the BE deletes them |

A version range such as `[2-11]` covers versions 2 to 11, not ten orders. On this sandbox, cumulative compaction usually merges the ten small rowsets within seconds, so what you see depends on when you look; the business result does not.
Only read the status here; do not trigger compaction or modify storage files.
[Tablet status access](https://doris.apache.org/docs/4.x/admin-manual/trouble-shooting/tablet-local-debug/)

### What Does a Query Actually Read?

EXPLAIN shows what a query plans to read; the **Query Profile** records what it did. With `enable_profile = true` in a session, Doris keeps a Profile for each query; `SELECT last_query_id()` right after a query returns its query ID, which is also its Profile ID, and the FE serves the Profile's text and shows it on the QueryProfile page of its Web UI. Lab 2 turns Profiles on for its session, also turns off the SQL cache so that a repeated query really scans, compares three queries on `orders_batch`, and restores both settings afterwards.

A scan's row counters tell how it found its rows:

| Counter | What it counts |
| --- | --- |
| ScanRows | Rows the scan read from its segments |
| RowsKeyRangeFiltered | Rows the sort key's index let it skip without reading |
| RowsVectorPredFiltered | Rows it read and then dropped with the filter |
| RowsRead | Rows that passed the filter |

All three of Lab 2's queries return order 1, but `WHERE order_id = 1` reads one row, because `order_id` is the sort key and its index narrows the range, while `WHERE customer_id = 832` reads all ten rows and drops nine. Selecting all columns instead of two, with the same filter, reads more bytes: a column store reads only the columns a query uses. On ten rows these counters describe the kind of work, not its speed.
[Configuring and viewing Profiles](https://doris.apache.org/docs/4.x/query-acceleration/query-profile/)

### How Should You Interpret Observations?

| Observation | Main purpose | What to keep in mind |
| --- | --- | --- |
| Row counts, details, amounts | Check the business data saved by both write methods | Confirm matching results before comparing write throughput |
| EXPLAIN | Inspect planned scan scope and filter conditions | Query duration and disk IO require runtime statistics |
| Version-related Tablet metadata | Inspect storage state at sampling time | Complete Rowset/Segment lists require further storage inspection |
| Query Profile | Inspect actual operator durations, scan statistics, and other execution metrics | Keep machine, cache, data size, and other conditions consistent when comparing performance |

Background compaction may finish before sampling. If the two tables have similar version-related information, first confirm the write methods,
then interpret the result in light of the sampling time; one sample reflects storage state at that moment.
This section uses ten rows to understand write mechanisms. Evaluating production throughput also requires fixed data size, concurrency, and machine resources,
with sustained observation of write latency, version accumulation, and whether compaction keeps up with writes.

## Hands-on Lab 2: Observe Storage and Write Batches

Before starting, complete Module 1, be able to connect to the lab instance and verify order totals, and use the course's dedicated lab database.

Open [Lab 2](lab02_observe_storage.ipynb) and complete these steps in order:

1. Create identically structured tables and write the same ten orders in one batch and in ten single-row writes.
2. Verify that both tables contain ten rows and 12220.60.
3. Read the partitions' and tablets' versions, then the rowsets of each tablet and what compaction did with them.
4. Compare a query's plan with the scan counters of its Query Profile.

### Data Source and Notes

The lab uses a historical subset of Microsoft's official WWI simulated wholesale business, retaining the original customer and product identifiers.
See the [data notes](../../datasets/README.md) for fields, business definitions, and expected results.

See the [Level 1 extension labs](../extensions/README.md) for additional operations. They use separate `ext_*` tables rather than repeatedly rewriting this lab's business results.

## Module Summary

- The client submits SQL, FE plans and distributes tasks, and BE reads column data and performs filtering, aggregation, and other computation.
- Table, Partition, Tablet, Rowset, and Segment represent different levels: table, range, shard, versioned data set, and columnar file.
- Write visibility depends on transaction publication and interface semantics. Compaction improves physical organization in the background and should not change business results.
- Keep data, table structure, and configuration consistent when comparing batches. Both tables in this lab must contain ten rows and 12220.60.
- Plans, metadata, and runtime statistics serve different purposes; small samples and a single observation cannot prove a fixed performance gain.

## Knowledge Quiz 2: Doris Storage Architecture and Write Mechanisms

After completing the notes and lab, open [Quiz 2](quiz02_storage_and_write_batches.ipynb).
The quiz contains five single-choice questions and does not depend on Doris or external services. Read the answer explanations after submitting.

Next module: [Module 3: Doris Table Models, Partitioning, and Bucketing](../module03-table-design/course.md).

## Official References

- [System architecture](https://doris.apache.org/docs/4.x/features-architecture/system-architecture/)
- [Partitioning and bucketing basics](https://doris.apache.org/docs/4.x/table-design/data-partitioning/basic-concepts/)
- [Compaction principles](https://doris.apache.org/docs/4.x/admin-manual/trouble-shooting/compaction-principles/)
- [EXPLAIN](https://doris.apache.org/docs/4.x/sql-manual/sql-statements/data-query/EXPLAIN/)
- [Query Profile](https://doris.apache.org/docs/4.x/query-acceleration/query-profile/)
- [Group Commit](https://doris.apache.org/docs/4.x/data-operate/import/load-best-practices/group-commit-manual/)
