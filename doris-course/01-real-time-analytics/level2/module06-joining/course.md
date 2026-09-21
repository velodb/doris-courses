# Module 6: Joining data

## Goal

Combine a fact sample with a product dimension and connect logical join
semantics to Doris distributed execution plans.

## Learning outcomes

By the end of this module, learners can:

- choose inner, left, semi, and anti joins from the required result semantics;
- predict result-grain changes caused by duplicate dimension keys;
- distinguish build and probe inputs in a hash join;
- use `EXPLAIN` to identify join conditions and data movement;
- explain why a small dimension is commonly broadcast while large inputs may be
  shuffled.

## Lab

[`lab6_join_data.ipynb`](lab6_join_data.ipynb) creates an isolated fact sample
and one-row-per-key dimension, verifies matched and orphan rows, compares row
counts, and reads the distributed plan. It does not change the shared event
history.
