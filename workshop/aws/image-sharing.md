# Prepare a private course image for sharing

The publisher's acceptance target is a **source-owned image that meets the
prerequisites for private sharing**, plus a complete recipient handoff. The
publisher never needs a recipient profile, password, access key or permission
to assume a recipient role. Recipient copy, validation and workshop deployment
are performed by the recipient using their own credentials and are recorded
separately from source readiness.

The transfer workflow creates a separate customer-managed-key (CMK) encrypted
distribution copy, then lets one named recipient copy it into their own account
under a new recipient-owned CMK. The original published AMI, its snapshot, its
release tags and its release file stay unchanged. The recipient copy is a
**candidate** until it passes the existing independent-instance audit, complete
selected course, real browser and cleanup gates in [image validation](image-validation.md).

The tools and Terraform module have offline coverage. Actual source-side CMK
copy/readiness verification is pending a source budget and retention window.
Recipient-side cloud/course/browser results remain unverified until supplied
by the recipient; they are not prerequisites for publisher qualification.
Existing [support claims](support.md) do not certify cross-account delivery.
The documented recipient workflow uses the same commercial AWS Region, Linux x86_64, one
200 GiB EBS root and the exact released profile set. Schema-v1 releases and
schema-v2 content bindings are preserved; this does not extend any course's
tested language, Region, profile or browser scope. Cross-Region transfers and
direct activity launches from a foreign-owned shared AMI are outside this flow.

## Ownership and access

| Artifact | Owner | Access and lifecycle |
| --- | --- | --- |
| Original validated AMI and snapshot | Publisher | Existing private release; never managed by the transfer state |
| Distribution AMI, snapshot and CMK | Publisher | Separate copy; AMI/snapshot grants name one recipient account; CMK policy names one IAM user or role |
| Imported candidate, snapshot and CMK | Recipient | Private, independently re-encrypted copy; no sharing grants |
| Published recipient release and validation/activity state | Recipient | Existing validation and prebuilt activity lifecycle |

An AWS-managed `aws/ebs` key cannot be shared. Copying a shared AMI also needs
read access to its backing snapshot; AMI launch permission alone is insufficient.
Cross-account KMS use requires both the source key policy and the recipient's
IAM permissions. See the AWS guides for [AMI sharing](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/sharingamis-explicit.html),
[cross-account copying](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/CopyingAMIs.html)
and [encrypted snapshot key permissions](https://docs.aws.amazon.com/ebs/latest/userguide/share-kms-key.html).

The module grants only the documented KMS copy operations to the exact recipient
principal, restricts cryptographic use to EC2 in the chosen Region and restricts
`CreateGrant` to grants created by an AWS service. The owner administration
statement delegates to that key's owning account. Its `Resource: "*"` refers
to the attached key; no public or wildcard principal is used. Account
administrators can change their own IAM policies and assume their own roles.
This is not a boundary against a recipient account's administrators.

For source preparation, confirm the source account/profile, Region, course
selection, source-side budget and retention review time. No recipient identity
is needed at this stage. At actual grant time, obtain only the recipient's
account ID, exact IAM user/role ARN and agreed copy window. The recipient keeps
their profile and credentials in their own environment. Each side manages its
own costs and cleanup. Install the existing
[preflight dependencies](preflight-requirements.txt) and Terraform 1.10+.
The module pins the same AWS provider as the other workshop modules.

## Prepare the source image without a recipient

Use the exact publisher release, sealed manifest, original builder record and
matching runtime bundle. The package includes only the three selected
provenance files and runtime payload files named by its checksummed manifest.
It excludes source operator bindings, state, plans, profile configuration and
credentials. In particular, the recipient does not need paths on the publisher's
computer. The runtime bundle allows the existing offline/live preflight to
verify the exact baked runtime without rebuilding the image.

Choose a new transfer ID and private directories for the version being prepared.
Omitting both recipient options creates an unbound package.

```bash
python tools/workshop_image_share.py prepare \
  --release private/release.json --manifest private/image-manifest.json \
  --builder private/build-instance.json --runtime-bundle private/runtime-bundle \
  --transfer-id rta-partner-v1 \
  --output workshop/aws/.local/transfer-input
```

`prepare` is offline and prints the SHA256 of `package.json`. No command logs
credentials. Copy the printed digest into `INPUT_SHA256` in the next command.
`REVIEW_TIME` must be a future RFC3339 timestamp. `SOURCE_BUDGET_USD` is the
agreed source-side budget. This value is a declaration, not a price estimate,
AWS Budget configuration or hard spending cap. Review current AWS pricing and
the source distribution snapshot, dedicated CMK and retained source artifacts
before approving the source plan. The recipient separately budgets their own
copy/key, validation instances and workshop resources.

```bash
python tools/workshop_image_share.py plan --mode export --action stage \
  --package workshop/aws/.local/transfer-input --trusted-package-sha256 INPUT_SHA256 \
  --profile publisher-profile --budget-usd SOURCE_BUDGET_USD \
  --retain-until REVIEW_TIME --run-dir workshop/aws/.local/transfer-source
```

Read the printed attempt directory's `review.json` and `binding.json`. The only
managed resources are one enabled dedicated CMK with an owner-only policy and
one AMI copy. No external AMI, snapshot or key-policy grant is created. The
original published image is not a managed resource. Obtain the plan SHA256
from `binding.json` after review:

```bash
python tools/workshop_image_share.py apply \
  --run-dir workshop/aws/.local/transfer-source --attempt SOURCE_PLAN_DIRECTORY \
  --approved-plan-sha256 REVIEWED_SOURCE_PLAN_SHA256
python tools/workshop_image_share.py status --run-dir workshop/aws/.local/transfer-source
python tools/workshop_image_share.py readiness --run-dir workshop/aws/.local/transfer-source
```

`readiness` rechecks the original release, available owned copy, encrypted root,
enabled customer-managed key, owner-only policy, absence of image/snapshot
grants and the exact manifest/runtime/copy lineage. It writes a timestamped
source-side report with `share_ready: true`, `recipient_credentials_required:
false` and `cross_account_copy_verified: false`. Successful source readiness
does not claim that a recipient has copied or deployed the image.

## Grant access when a recipient is known

Ask the recipient for their 12-digit account ID and exact receiving IAM role
or user ARN. They do not need to trust the publisher to assume that role;
the ARN identifies the principal authorized to use the source encryption key.
The following account and role are fictional.

```bash
python tools/workshop_image_share.py bind \
  --run-dir workshop/aws/.local/transfer-source \
  --recipient-account 111111111111 \
  --recipient-principal arn:aws:iam::111111111111:role/WorkshopImporter \
  --output workshop/aws/.local/transfer-bound
python tools/workshop_image_share.py plan --mode export --action share \
  --package workshop/aws/.local/transfer-bound --trusted-package-sha256 BOUND_PACKAGE_SHA256 \
  --profile publisher-profile --run-dir workshop/aws/.local/transfer-source
```

`bind` only writes a recipient-bound package and preserves the previous binding;
it makes no cloud calls and invalidates earlier plans. `share` creates a new
reviewed plan for the existing source copy: one exact AMI account grant, one
snapshot account grant and an update of the dedicated key policy for the named
recipient. It cannot replace the staged image/key. Review and apply the new
attempt using `apply --approved-plan-sha256`, then:

```bash
python tools/workshop_image_share.py export \
  --run-dir workshop/aws/.local/transfer-source --output workshop/aws/.local/partner-delivery
```

The receiver keeps the same Region and performs all receiving operations under
their own identity. Source staging, binding, sharing, revocation and source
cleanup never load a recipient profile. Keep the agreed source retention/copy
window open until the recipient confirms their copy is complete. Do not edit
expired run metadata to extend it; review a new transfer/window explicitly.

Each run has its own local Terraform state and data directory. Keep the entire
run private and backed up for cleanup. It is separate from foundation, activity
and source-image build state. `apply` checks the reviewed hash, account,
package, inputs, tool/module hashes, state hash and 15-minute plan window.
It rechecks the source release before creation, and never dispatches the same
attempt twice. The provider bounds the AMI copy wait to 120 minutes; the copy
can outlive a local timeout. Tags and retention timestamps do not delete anything.

The publisher needs EC2 copy, tagging, image/snapshot permission and cleanup
permissions plus management of the dedicated CMK. Derive baseline operator IAM
permissions from the actual rendered Terraform plan, then review and scope them
to the selected account, Region, source AMI and transfer resources:

```bash
uvx iam-policy-autopilot@latest generate-policies SOURCE_PLAN_DIRECTORY/review.json \
  --region us-east-1 --account 000000000000 --pretty
```

This produces policy documents only. Attach reviewed permissions through your
normal account administration process. The recipient also needs its own copy,
CMK-management and validation permissions, plus the source CMK permissions
listed in the AWS encrypted-snapshot guide, scoped to the distribution key ARN.
Do not interpret the source key policy as a substitute for recipient IAM.

## Recipient handoff

Give the recipient the [self-service receiving runbook](image-sharing-recipient.md).
Include the source account, Region, distribution AMI/snapshot IDs, CMK ARN,
course/release/profile and runtime binding, verified operator revision or Git
bundle, delivery package and independently trusted digests, copy window and
contact. The original source AMI ID is provenance; the newly CMK-encrypted
distribution AMI ID is the one they copy.

Deliver the `partner-delivery` directory through an approved private channel.
Send the `export` command's printed package SHA256 through an independently
trusted channel; a checksum bundled with untrusted files does not establish
publisher identity. Do not hand over the source state or AWS credentials.

In the recipient account, use the exact named IAM principal and the supplied
package digest. Sharing tags are not required for discovery: other accounts
cannot generally see the publisher's AMI tags. The tool pins the AMI ID and
checks its actual owner, copy lineage, encrypted snapshot and CMK instead.

```bash
python tools/workshop_image_share.py plan --mode import --action create \
  --package private/partner-delivery --trusted-package-sha256 DELIVERY_SHA256 \
  --profile recipient-profile --budget-usd RECIPIENT_BUDGET_USD \
  --retain-until REVIEW_TIME --run-dir workshop/aws/.local/transfer-recipient
# Review the recipient plan, then:
python tools/workshop_image_share.py apply \
  --run-dir workshop/aws/.local/transfer-recipient --attempt RECIPIENT_PLAN_DIRECTORY \
  --approved-plan-sha256 REVIEWED_RECIPIENT_PLAN_SHA256
python tools/workshop_image_share.py candidate \
  --run-dir workshop/aws/.local/transfer-recipient --output workshop/aws/.local/imported-candidate
```

The recipient plan creates one new CMK and one AMI copy, with no sharing grants.
`candidate` checks their owner, lineage, private image/snapshot permissions,
key policy, key state and provenance tags. It writes the existing candidate
interface and an import receipt. It preserves the sealed manifest bytes and
original builder identity for the fresh-instance audit. Repeated candidate
checks use only recipient-owned cloud resources and these portable records.

Follow [the existing validation commands](image-validation.md) with
`--candidate-run workshop/aws/.local/imported-candidate`. Use a new recipient
event/profile/state and one independent validation learner; set the pilot's
`runtime_bundle_dir` to the delivered `runtime` directory. Preserve the exact
course SHA, profile set and, for v2, content binding. Execute the full course
and real browser checks on the same audited fresh instance, destroy and
reconcile it, then publish a **new recipient release** through
`tools/workshop_validation.py publish`. No source validation record is reused
as recipient execution evidence.

Only after publication, use the recipient's AMI/owner/snapshots and release
file in the unchanged [prebuilt activity flow](prebuilt.md). It continues to
reject foreign-owned, shared, unencrypted, unpublished or mismatched images.
Run the required learner isolation and activity cleanup checks in the recipient
account; successful copying alone does not certify them.

## Revoke, recover and clean up

After the recipient reports their copy complete, make a fresh source `plan` using the
same package, profile and run directory with `--mode export --action revoke`.
Review and apply that attempt through the same hash gate. Revocation removes
the AMI and snapshot grants, removes recipient key-policy statements and
**disables the dedicated export CMK**, blocking any outstanding KMS grants.
`status` verifies the resulting private permissions and disabled key.

This blocks future use of the distribution copy. It does not erase recipient
copies already re-encrypted with the recipient key, recipient release files,
running instances or exported course/data copies. In-progress copies can fail
when access is revoked. Existing KMS grants can remain recorded on the disabled
source key; policy removal alone does not revoke them. See AWS guidance on
[KMS grants](https://docs.aws.amazon.com/kms/latest/developerguide/grants.html).
Use a new transfer for a new sharing window.

Repeated planning may produce no-ops. Reapplying an already dispatched attempt
is rejected, including after an uncertain timeout. Inspect its private state,
actual resource IDs and AWS status before making a **new reviewed plan in the
same run**. Never delete state to retry. Replacement plans are refused; clean
up failed artifacts before starting a new transfer ID/run. Expired retention
windows allow revocation and destruction, not new sharing/copy operations.

For each side, use the same run and package with `--action destroy` to produce
a fresh destruction plan. Review the exact copy, grant and key removals before
applying. Never destroy a recipient copy/key while its release or activity is
still needed. First clean up validation and activity state through their
existing workflows. Keep the original publisher release outside this cleanup.

```bash
python tools/workshop_image_share.py reconcile \
  --run-dir workshop/aws/.local/transfer-source --attempt SOURCE_DESTROY_PLAN_DIRECTORY
python tools/workshop_image_share.py reconcile \
  --run-dir workshop/aws/.local/transfer-recipient --attempt RECIPIENT_DESTROY_PLAN_DIRECTORY
```

Reconciliation checks empty state, exact image/snapshot IDs, transfer-tagged
images/snapshots and all owned snapshots using the dedicated CMK. It records
the remaining KMS key state and grants. CMK destruction schedules deletion
after seven days: **PendingDeletion is an explicitly retained, unusable key,
not a deleted resource**. Preserve the run and repeat reconciliation to confirm
final deletion. Recycle Bin retention, delayed snapshot deletion or an orphan
snapshot leaves cleanup incomplete. Keep failure records and do not report
zero residual resources until both accounts' actual APIs support that claim.

Snapshot and key retention is separate from learner TTL. Neither activity
destruction nor retention tags retire these artifacts. Record who retains
each copy/key, review time and continuing storage/key costs in both accounts.
