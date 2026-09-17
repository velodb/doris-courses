# Synthetic order fixtures

These original synthetic fixtures contain no real customers or external data.
All timestamps use Asia/Shanghai. The fixed business cutoff is
2026-01-02 12:00:00; ingestion time is separate.

- `orders.json` / headerless `orders.csv`: ten CREATED orders, versions 1,
  order amounts totaling 1400.00.
- `raw_orders.json`: those ten rows plus invalid amount and missing order ID.
- `malformed_orders.csv`: two rows, one invalid amount, for whole-batch rejection.
- `deliveries.json`: six different events, seven deliveries in order
  E02, E01, E03, E04, E05, E06, E02.
- `expected_current.json`: hand-specified complete final after-images, not
  computed by the notebook under test.
- `expected_summary.json`: independently specified acceptance totals.

A status change includes the full after-image. The pedagogical version is an
increasing integer per order, **not** a Binlog position or a timestamp.
Same-event retries carry identical business fields. S1001…S1010 identify
initial snapshots; E01…E06 identify later business changes. Delivery IDs
identify arrivals, so replay may increase raw delivery count without changing
the 16-event logical history or 11-order current state.

Refunds preserve payment history: paid GMV is 250.00, refund amount 150.00.
Cancelled and refunded orders remain in the main dataset. DELETE exercises
use an independent table. These are correctness fixtures, not a benchmark.

CSV columns in order:
order_id, customer_id, order_amount, status, event_version, event_id,
event_time, paid_amount, refund_amount, region.
