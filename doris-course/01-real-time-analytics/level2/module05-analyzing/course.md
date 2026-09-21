# Module 5: Analyzing data

## Goal

Turn the typed event model from Module 4 into business metrics while keeping the
result grain explicit.

## Learning outcomes

By the end of this module, learners can:

- use `WHERE`, `GROUP BY`, `HAVING`, and `ORDER BY` for analysis;
- combine scalar, aggregate, and conditional aggregate functions;
- use CTEs to name intermediate grains;
- use `ROW_NUMBER`, `LAG`, and cumulative windows with deterministic ordering;
- explain why aggregation reduces rows while a window function preserves rows.

## Lab

[`lab5_analyze_data.ipynb`](lab5_analyze_data.ipynb) answers daily and regional
funnel questions, ranks products, and compares daily revenue with its previous
value. It reuses `modeling_typed_events` from Module 4 and does not mutate the
Level 1 `events` baseline.
