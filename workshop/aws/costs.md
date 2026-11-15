# Cost and validation observations

Costs depend on Region, runtime, storage, requests and retained resources.
Obtain a new quote before every reviewed deployment. `budget_usd` and the
preflight estimate are admission checks, not an AWS spending cap. Keep enough
time and budget to reconcile a partial failure and retire every owned resource.

## A recorded dual-course budget model

The 2026-11-14 live Price List queries in `us-east-1` produced the following
conservative model for three learners in each of RTA and Data Warehousing.
The compute reserve includes image building, independent validation and activity
hosts. It is larger than a single six-host classroom's measured duration.

| Component | Reserved usage | Model USD |
| --- | --- | ---: |
| Linux `m7i.2xlarge` compute | 60 host-hours at 0.4032/hour | 24.1920 |
| Portal `t3.small`, Standard credits | 8 hours at 0.0208/hour | 0.1664 |
| Encrypted gp3 roots | 200 GiB per course host, 20 GiB portal; prorated reserve | 1.4476 |
| Public IPv4 | 84 address-hours | 0.4200 |
| ALB | 8 hours | 0.1800 |
| ALB capacity | 80 LCU-hours | 0.6400 |
| Internet egress | 50 GB | 4.5000 |
| New image snapshots | Two full 200 GiB source volumes, seven-day reserve | 5.0000 |
| S3 storage, writes and reads | 2 GB-months; 60,000 writes and 60,000 reads | 0.3700 |
| DNS/authentication allowance | Fixed reserve | 2.0000 |
| Contingency | Fixed reserve | 10.0000 |
| Total | Calculated from unrounded Decimal inputs | **48.9160** |

The storage model uses a conservative 28-day month for its conversion. The
seven-day full-volume snapshot reserve is a budgeting assumption; a particular
run may require immediate image retirement. Snapshot billing depends on stored
data, not simply the source volume capacity. Capture the current price quote,
its timestamp/units, actual launch/deletion times and the explicit retention
policy with the private event records. These numbers are observations for this
Region and date, not a standing price quote for a future partner.

## Reported costs and attribution

A Cost Explorer query on 2026-11-14 reported **8.9664788628 USD** of whole-account
`UnblendedCost` for UTC 2026-11-13, excluding credits and refunds. The response
still had `Estimated=true`. It included retained baseline resources as well as
the previous validation run. It is not an event-only charge or a finalized
invoice. The 48.9160 USD reserve must not be presented as that run's actual bill.
Current-day results can lag substantially; a near-zero result during a live
workshop does not establish negligible spending.

Operators can repeat the read-only query with their own profile:

```bash
aws --profile "$PARTNER_PROFILE" --region us-east-1 ce get-cost-and-usage \
  --time-period Start=YYYY-MM-DD,End=YYYY-MM-DD \
  --granularity DAILY --metrics UnblendedCost \
  --group-by Type=DIMENSION,Key=SERVICE \
  --filter '{"Not":{"Dimensions":{"Key":"RECORD_TYPE","Values":["Credit","Refund"]}}}'
```

Use the current UTC dates; the end is exclusive. Sum grouped amounts with
Decimal or another deterministic calculator. When event attribution is needed,
configure and verify the account's cost-allocation tags before the event.
Record the billing scope, whether tag data was available and whether results
were estimated. Never label an unfiltered account total as one workshop's bill.

## Current event and cleanup timing

A query at 2026-11-15 01:41 UTC reported only **0.0315986581 USD** for the whole
account's UTC 2026-11-14 `UnblendedCost`, excluding credits and refunds, with
`Estimated=true`. It had not established this event's compute charges. It is
not an event bill, a finalized invoice or evidence that the event cost that
amount. The earlier 48.9160 USD reserve remains a planning model.

The 2026-11-14/15 validation exceeded its approved eight-hour shared-resource
retirement window. Learner/build/validation instances were absent when checked,
but the shared portal, ALB and temporary images still required explicit cleanup.
Record their actual retirement timestamps and the overdue interval separately;
instance expiry does not establish complete or on-time resource retirement.
Stop new work after the authorized deadline and continue cleanup. A later paid
validation needs a new explicit budget/window and fresh reviewed plans.

## Retained resources and observations

Track these separately after activity cleanup:

- Private image snapshots until their explicit retirement date. AMI metadata
  and the snapshot lifecycle are separate; deregistering an AMI does not prove
  its snapshot was deleted.
- Private/versioned foundation state and its old versions, plus any separate
  retained dataset storage and requests.
- Long-lived DNS hosted zones and queries, approved parent delegation and any
  deliberately retained shared ALB/portal resources.
- Logs, artifacts and diagnostic evidence under their declared retention policy.

The 2026-11-13 combination retired all newly created image/build/validation,
activity and shared-entry resources. Final proof covered twelve known instances
and root volumes, six remote and five local state files, exact service/network
IDs and preservation of the original OBS image/snapshot, DNS zone, foundation
and dataset. This proves the recorded cleanup scope, not the absence of every
unrelated resource in another account or Region.

Read [support and validation](support.md) for the actual course/browser scope,
limitations and timings. A readiness timestamp or a small test is not a
performance guarantee. DNS and snapshot retention tags do not implement cleanup.
For lifecycle and reconciliation steps use [the organizer guide](organizer-guide.md).
