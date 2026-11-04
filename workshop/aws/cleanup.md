# Cleanup ownership and reconciliation

`tools/workshop_cleanup.py` prepares ownership evidence, collects read-only AWS
inventory and reconciles it with post-cleanup Terraform exports. It never deletes
resources or grants deletion permission. These interfaces have local fixtures
and SDK contract tests. The reviewed shared activity cleanup has also been
exercised on AWS; account-wide residual coverage, target scale and TTL remain
separate acceptance work.

## Prepare evidence before cleanup

Export each selected module with `terraform show -json` using its explicit
operator backend and Terraform data directory. Keep raw exports private: they
can contain user data, policies and Cognito secrets. Then create a fresh manifest:

```bash
python tools/workshop_cleanup.py prepare \
  --config workshop/aws/events/event.local.yaml \
  --state pilot=workshop/aws/.local/pilot-before.json \
  --state access-bindings=workshop/aws/.local/access-before.json \
  --state-bucket YOUR_EXISTING_FOUNDATION_BUCKET \
  --history workshop/aws/.local/event-run-04/lifecycle.json \
  --shared workshop/aws/.local/access.json \
  --routes workshop/aws/.local/access-plan.json \
  --retained workshop/aws/.local/retained.json \
  --output workshop/aws/.local/cleanup-manifest.json
```

Use only `pilot` when auditing a pilot without shared bindings. Even empty
exports need their real Terraform JSON format/version. Unsupported resources,
nested modules, missing ownership tags and shared/foundation deletion states
are rejected. Event expiry does not block inspection or the existing destroy.

Taggable roots require exact Project/Owner/Event/Scope. Untagged S3 controls and
objects, inline IAM policies, route associations, target attachments and Cognito
clients/domains require owned parents. DNS records require the exact assigned
host, zone and ALB. State ARNs must match the explicit account and Region.
Shared-root records and another partner namespace cannot become activity claims.

`--retained` is an optional private JSON array of exact `{ "kind": ..., "id": ... }`
references, including published `images` and `snapshots`. Foundation storage and
supplied shared VPC/ALB/gateway/portal references are retained automatically. A
resource cannot be both activity-owned and retained. The public zone, apex alias
and independent portal host have separate ownership, cost and retirement duties.

History preserves withdrawn/replaced instance, root-volume and ENI references.
When the route plan contains its validated listener ledger, withdrawn service
hostnames are also retained as exact DNS scan references.
Temporary image work can supply its separate `image-build` state and exact
`--build-id`, or `image-validation` state with matching event ownership. An
explicitly associated history file may also include `image_build_ids`,
`known_images`, `known_snapshots` and `known_key_pairs` for earlier attempts.
Preserve original receipts; never infer ownership from a name prefix. Candidates
for those build IDs remain residuals. Validated images and their backing
snapshots are protected. Retention timestamps do not implement deletion.

The manifest contains selected identifiers, parent relationships and proposed
module order. It omits raw state, user data, policies, client secrets, users,
grades and object contents. `prepare` makes no AWS calls. This evidence is not
a reviewed saved plan or an apply grant.

## Separately reviewed cleanup

Remove activity bindings before learner hosts, then clean the pilot and explicitly
associated temporary image states. Preserve shared resources, foundation,
published images and other activities. The manifest records that order; this
interface does not execute it. Use the existing bound pilot lifecycle and
separately reviewed plans for other states. Do not automatically adopt or delete
a discovered orphan. Retain predecessor runs and ownership evidence after any
failure, re-read state, review new plans as needed, and reconcile again.

The [pilot destroy](lifecycle.md#destroy-the-activity) now requires a complete
delete-only plan, unchanged refreshed IDs/tags/relationships and exact owned
parents. Missing, duplicate, extra, non-delete or deferred changes are rejected.
Explicit matching `resource_drift` deletion evidence can account for resources
already removed remotely. A stale tagless dependency lacking its owned parent
can only be forgotten with that evidence; it cannot authorize a fresh delete.
It checks all pages for known EC2/EBS/ENI residuals. A lingering ENI leaves cleanup
incomplete. Its boundary remains pilot state and known resources.

### Reviewed shared activity cleanup

`tools/workshop_shared_cleanup.py` prepares two private saved destroy plans and
applies activity bindings before the pilot under one activity fence. It retains
the shared entry, portal, DNS zone, foundation and published images. Remove the
activity from the separately managed portal registry first. This command does
not retire the portal or shared infrastructure, revoke unrelated identities or
perform the full residual scan.

Use the latest pilot run, the bindings directory containing its original
`inputs.tfvars.json` and `backend.tfbackend.json`, and the exact current route
plan whose `terraform` object matches those variables. The bindings backend
must use the activity's canonical `access.tfstate` key and the same account,
Region and state bucket as the pilot. This first version supports a DNS zone in
the workload account; cross-account DNS needs separate cleanup and validation.
Cancel any enrolled TTL revision before planning or applying a shared cleanup.
Unenrolled activities still require operators to coordinate; local run locks
are not distributed locks.

```bash
python tools/workshop_shared_cleanup.py plan \
  --pilot-run workshop/aws/.local/event-run \
  --bindings-run workshop/aws/.local/event-bindings \
  --routes workshop/aws/.local/access-plan.json \
  --output workshop/aws/.local/shared-cleanup-review

# Inspect both private destroy.tfplan files and review.json. Then pass the
# review_sha256 printed by the plan command.
python tools/workshop_shared_cleanup.py apply \
  --pilot-run workshop/aws/.local/event-run \
  --bindings-run workshop/aws/.local/event-bindings \
  --routes workshop/aws/.local/access-plan.json \
  --output workshop/aws/.local/shared-cleanup-review \
  --approved-review-sha256 REVIEWED_SHA256
```

For an enrolled activity, supply `--ttl-binding` on both commands. The review
expires after 15 minutes and binds the inputs, source, initialized backends,
manifest and both plans. Terraform obtains its normal state locks for each
plan and apply. A failed or interrupted apply is never replayed automatically:
retain the private progress record, reconcile actual state and any TTL fence,
then create fresh reviewed plans. There is no cross-state rollback. A completed
pilot reconciliation checks known instance, root-volume and ENI IDs; it is not
proof of complete DNS, Cognito, target-group or account-wide cleanup. Run the
separate [collection and reconciliation](#collect-and-reconcile) workflow.

## Collect and reconcile

Export fresh post-cleanup state from the same backends, including empty states.
`collect` is an explicit read-only AWS action using the selected profile/account
and configured Region. `report` is entirely offline:

```bash
python tools/workshop_cleanup.py collect \
  --config workshop/aws/events/event.local.yaml \
  --manifest workshop/aws/.local/cleanup-manifest.json \
  --state pilot=workshop/aws/.local/pilot-after.json \
  --state access-bindings=workshop/aws/.local/access-after.json \
  --output workshop/aws/.local/cleanup-snapshot.json
python tools/workshop_cleanup.py report \
  --manifest workshop/aws/.local/cleanup-manifest.json \
  --snapshot workshop/aws/.local/cleanup-snapshot.json \
  --output workshop/aws/.local/cleanup-report.json
```

A snapshot is bound to the exact manifest. Missing state proof, remaining state
addresses, missing/failed catalog coverage and ownership conflicts block success.
State emptiness alone cannot pass. Partial results and eventual-consistency
residuals remain visible; repeat collection into new files after reconciliation.
Identical offline evidence produces identical reports.

Catalogs cover EC2/EBS/ENI, VPC/subnet/routes/security groups, Internet/NAT gateways,
EIPs, owned-account AMIs/snapshots/key pairs, IAM roles/profiles, runtime buckets
and SSM documents. Shared-binding coverage adds ALBs/TGs/targets/rules, exact DNS
records and activity Cognito pools/clients/domains. Some child absence follows
from absent owned parents; domains also get exact historical checks. Bucket
absence implies no objects, versions or multipart uploads in that bucket.

Paginated tagged discovery can reveal objects outside the module catalog,
including future cleanup resources. It does not find every untagged object.
Exact history, network links and assigned host/record references identify
untagged conflicts for review. Another activity in the same shared VPC is not
an owned dependency. Foreign target groups referencing a learner are reported,
never automatically deregistered. No resource-name prefix grants ownership.

Reads have finite SDK timeouts/retries, page bounds and a request budget. A
permission error, pagination limitation or bound makes coverage incomplete.
Partial resources remain recorded and raw errors are discarded. Unrelated
account inventory is filtered before persistence. The scanner reads no IAM
policy documents, Cognito users/secrets, SSM document bodies or S3 object contents.
When an individual bucket cannot be inspected, the scanner continues with the
remaining buckets but marks the entire bucket catalog incomplete. It cannot
assume that an inaccessible bucket is unrelated to the activity.

Reports separate residuals, conflicts, incomplete coverage, remaining state,
known absence and protected resources. Protected resources require cost review;
the report does not calculate their bill. `ok` applies to the supplied module
and state evidence at collection time. `account_globally_empty` is always false.
Cross-account DNS, full untagged discovery and real end-to-end cleanup remain
unverified. No progress, grades, certificates or automatic archives are added.

Exit codes: `0` for prepared evidence, fully collected requested catalogs, or a
passing report; `1` for incomplete catalogs or a non-passing report; `2` for invalid
input, ownership/identity failure or unwritable output. Successful collection
alone does not mean cleanup passed. Outputs are private and never overwritten.

References: [Tagging API limitations](https://docs.aws.amazon.com/resourcegroupstagging/latest/APIReference/API_GetResources.html),
[Route 53 pagination](https://docs.aws.amazon.com/Route53/latest/APIReference/API_ListResourceRecordSets.html),
[Cognito inventory](https://docs.aws.amazon.com/cognito-user-identity-pools/latest/APIReference/API_ListUserPools.html),
[target health](https://docs.aws.amazon.com/elasticloadbalancing/latest/APIReference/API_DescribeTargetHealth.html).
