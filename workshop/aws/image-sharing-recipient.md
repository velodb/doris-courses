# Receive a course image and build your workshop

You perform every receiving operation in your own AWS account. Keep your AWS
profile, SSO session, credentials and Terraform state in your environment.
Send the publisher only your 12-digit AWS account ID, exact receiving IAM
role/user ARN, requested Region/version and an agreed copy window. The
receiving role does not need to trust the publisher to assume it.

Publisher source readiness is separate from your copy/deployment acceptance.
The workflow below preserves the existing private-image publication gates;
copying alone does not mark the course or your workshop as validated.

## What the publisher supplies

| Item | Purpose |
| --- | --- |
| Publisher account ID and source Region | Identify the owner and location; this workflow uses the same Region on both sides |
| Distribution AMI ID, snapshot ID and enabled CMK ARN | Pin the actual shared resources; use the distribution AMI, whose CMK supports sharing |
| Course SHA, profile set, release reference and runtime/manifest/content hashes | Select the exact course and language/version capabilities |
| `partner-delivery/` and SHA256 of `package.json` | Portable provenance, `distribution.json`, original builder ID and exact runtime payload |
| Accessible operator revision or `workshop-operator.bundle`, with independently delivered SHA256 | Obtain the matching tools and course Git history without publisher-local paths |
| Copy deadline, retention agreement, contact and recorded support limits | Know when source access ends and which course/browser scope was actually validated |

The publisher can supply the operator branch as a Git bundle when a revision
has not been published to a shared repository:

```bash
# Publisher, after committing the intended operator revision:
git bundle create private/workshop-operator.bundle aws
```

This contains committed source and its reachable Git history. Local untracked
state, profiles and private run directories are outside the bundle. The
publisher records the exact operator commit, source commit and bundle digest
in the handoff. Verify digests using a trusted channel independent of the files.

## 1. Prepare your local operator environment

Clone the supplied repository and check out its exact operator revision, or:

```bash
git clone /received/workshop-operator.bundle doris-courses
cd doris-courses
git checkout OPERATOR_COMMIT
git cat-file -e COURSE_COMMIT
python3.12 -m venv .venv-workshop
.venv-workshop/bin/python -m pip install \
  -r workshop/aws/preflight-requirements.txt
```

Install the same supported Terraform version, AWS CLI and Session Manager
plugin described in [getting started](getting-started.md). Configure and sign
in to your own AWS profile. Check `aws sts get-caller-identity --profile YOUR_PROFILE`
and confirm the account and principal match what you supplied to the publisher.

Your administrator grants that principal the EC2 copy/tag/inspection and
dedicated-key management permissions required by your own Terraform plan,
plus use of the publisher's exact source CMK. Source key policy permission and
your IAM policy are both required. The documented EBS key permissions include
DescribeKey, Decrypt, ReEncrypt, GenerateDataKey/GenerateDataKeyWithoutPlaintext
and CreateGrant restricted to grants for AWS resources; see the
[AWS encrypted-snapshot guide](https://docs.aws.amazon.com/ebs/latest/userguide/share-kms-key.html).
Keep all source-key permissions scoped to the CMK ARN supplied in the handoff.

## 2. Copy into your own account

Place the verified delivery directory under a private path. Use the digest
supplied by the publisher, your profile, your budget and a future retention
review timestamp. The budget value is a declaration, not a hard spending cap.

```bash
.venv-workshop/bin/python tools/workshop_image_share.py plan \
  --mode import --action create \
  --package /received/partner-delivery --trusted-package-sha256 DELIVERY_SHA256 \
  --profile YOUR_PROFILE --budget-usd YOUR_COPY_BUDGET_USD \
  --retain-until YOUR_REVIEW_TIMESTAMP \
  --run-dir workshop/aws/.local/received-image
# Inspect the printed attempt's review.json and binding.json, then:
.venv-workshop/bin/python tools/workshop_image_share.py apply \
  --run-dir workshop/aws/.local/received-image --attempt YOUR_PLAN_DIRECTORY \
  --approved-plan-sha256 YOUR_REVIEWED_PLAN_SHA256
.venv-workshop/bin/python tools/workshop_image_share.py candidate \
  --run-dir workshop/aws/.local/received-image \
  --output workshop/aws/.local/received-candidate
```

The copy has your own AMI/snapshot IDs and is encrypted under your own new CMK.
It has no external sharing grants. Preserve this run and key while your image
or workshops depend on them. Notify the publisher when copying is complete so
the publisher can close the temporary sharing window. Completed, independently
re-encrypted copies remain yours after the publisher revokes source access.

## 3. Validate and publish your own release

Follow [fresh-image validation](image-validation.md) with
`--candidate-run workshop/aws/.local/received-candidate`. Use your own foundation
state bucket, new event/expiry, one validation learner and private settings;
set `runtime_bundle_dir` to `/received/partner-delivery/runtime`.

The validation event uses the documented base-mode configuration for its
identity/capacity/budget preflight; `--candidate-run` pins the actual copied AMI
for the validation instance. Preserve the exact delivered course SHA, profiles
and v2 content binding. Run the initial audit, selected complete course and
same-instance real browser checks, then destroy/reconcile the validation
environment. Use `tools/workshop_validation.py publish` with your reports to
produce your own private release JSON. Do not reuse publisher execution reports
as evidence from your instance or edit a source release to replace its AMI ID.

These validation gates are currently required by the guarded prebuilt CLI.
The publisher's source-readiness task can finish without receiving your
credentials or executing these steps on your behalf.

## 4. Configure and start your workshop

Copy [the prebuilt event example](examples/prebuilt.example.yaml) and
[pilot settings](examples/pilot.example.yaml) into your private operator directory.
Fill these with your own values:

- AWS account/profile, Region, owner/event/learner IDs, instance/disk sizing and expiry.
- `course_ref` and `course_profiles` exactly matching your new release.
- `image.mode: prebuilt`, your new `image.ami_id`, your own `image.owner_account_id`,
  matching Region/architecture/course/profile fields and your new `image.release_ref`.
- Pilot `image_release_file` pointing to your new release, `runtime_bundle_dir`
  pointing to the delivered runtime, and `state_bucket_name` pointing to your foundation bucket.

The supplied pilot example is a base-install shape: add `image_release_file`
for prebuilt mode. Use the schema version/content binding corresponding to
your delivered release; use [the multilingual example](examples/multilingual.example.json)
when its v2 capabilities apply. Follow [first-run prerequisites](first-run.md)
to prepare your private foundation/state and [prebuilt startup](prebuilt.md):

```bash
python tools/workshop.py plan --config private/event.yaml \
  --pilot private/pilot.yaml --run-dir workshop/aws/.local/your-workshop
# Inspect the saved plan and approve its actual hash:
python tools/workshop.py up --run-dir workshop/aws/.local/your-workshop \
  --approved-plan-sha256 YOUR_REVIEWED_PLAN_SHA256
python tools/workshop.py status --run-dir workshop/aws/.local/your-workshop
```

For a formal browser workshop, set up your own domain or delegated partner
subdomain, DNS/TLS, Cognito and shared entry through
[shared access](shared-access.md) and [authentication](cognito-access.md).
The image supplies the course runtime; your account supplies the workshop's
identity, routing, state and infrastructure. The pilot alone offers the
documented SSM entry and is not the complete shared HTTPS deployment.

## 5. Close the workshop and retain the image intentionally

Destroy/reconcile your activity and shared-entry resources using their own
original state and [cleanup procedures](cleanup.md). Keep the copied image,
snapshot and key only under your agreed retention policy; those have a
separate lifecycle from learner TTL. Their owner pays their ongoing costs.
If retiring your copied image, use the receiving transfer run's new reviewed
destroy plan and `reconcile`; the dedicated CMK has a seven-day scheduled
deletion period, recorded as PendingDeletion until its actual removal.
