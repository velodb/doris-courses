# Module 7: Updating and deleting data

## Goal

Maintain an analytical current-state table without confusing logical
visibility, partial updates, and physical reclamation.

## Learning outcomes

By the end of this module, learners can:

- choose Duplicate Key for immutable history and Unique Key for current state;
- use a sequence column to reject an older out-of-order version;
- distinguish full-row writes from partial column updates;
- compare business soft deletion with SQL `DELETE`;
- explain why logical deletion does not mean immediate file reclamation.

## Lab

[`lab7_update_delete_data.ipynb`](lab7_update_delete_data.ipynb) uses isolated
current-state, partial-update, and delete tables. It demonstrates Merge-on-Write,
version ordering, sparse patches, soft deletion, and SQL deletion without
modifying the Level 1 baseline.
