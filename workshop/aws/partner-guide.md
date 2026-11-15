# Partner deployment: build images in your own account

A partner clones the reviewed repository, builds and validates private images in
its own AWS account, and operates its own workshops. This delivery path needs no
publisher AMI, snapshot copy, KMS grant or company AWS credential. Image sharing
is outside the current delivery workflow.

Use the [support matrix](support.md) to select the tested course version,
profiles, Region and instance size. The English schema-v1 RTA, complete Data
Warehousing and Observability selections have different pinned evidence. Do not
assume the multilingual schema-v2 selection has the same image qualification.

## Ownership and the initial handoff

| Item | Owner and responsibility |
| --- | --- |
| Repository and fixed deployment/course SHA | Organizer supplies the reviewed version; partner keeps its Git history |
| AWS profile, IAM, quotas and billing | Partner; learner instances never receive deployment credentials |
| Foundation state bucket and its private bootstrap state | Partner; retained separately from activity cleanup |
| DNS namespace | Partner-owned domain or an approved delegated subdomain |
| Parent NS record for an approved company subdomain | Parent-domain administrator, once at onboarding |
| Child hosted zone, ACM, ALB and operator portal | Partner; portal is separate from learner instances |
| Images, encrypted snapshots and private release records | Partner; one qualified release per exact source/profile selection |
| Cognito accounts, learner routing and activities | Partner; separate pools and states per activity |
| Learner progress and files | Learner exports before expiry; partner retires temporary disks |

For company-subdomain onboarding, provide the partner ID, contact, public AWS
account ID/Region, course/profile selection, workshop size/dates, billing contact
and requested domain scope/expiry. Do not provide passwords, access keys or an
SSO session. The parent administrator returns the approved namespace and records
the four NS values exported by the partner's child zone. Later activities do not
need another change to the parent zone. This is DNS delegation, not permission
to read company state, Cognito or images.

## 1. Clone and authenticate

```bash
git clone REPOSITORY_URL doris-courses
cd doris-courses
git checkout --detach "$WORKSHOP_SOURCE_SHA"
python3.12 -m venv .venv-workshop
.venv-workshop/bin/python -m pip install -r workshop/aws/preflight-requirements.txt
aws login --profile "$PARTNER_PROFILE"
aws sts get-caller-identity --profile "$PARTNER_PROFILE"
```

The organizer must publish the reviewed ref to a repository the partner can
fetch before the handoff. A local or unpublished commit is not a usable partner
release. Use that exact repository/revision and verify the returned account ID. Existing IAM Identity Center users can use their named SSO
profile instead. Install Terraform 1.17.1, Packer 1.16.2, the pinned Amazon plugin
1.8.3 and the Session Manager plugin; verify official checksums. Docker is needed
on the EC2 image builder, not on every learner laptop. AWS login provides local
credentials; it does not grant the workload permissions.

The deployment identity needs the actions checked by live preflight and the
selected modules: EC2/network, S3/state, IAM roles/profiles and PassRole, SSM,
ALB/ACM, Cognito and the owned Route 53 zone. The builder and learner roles are
separate restricted runtime identities. Have the account administrator review
those module policies and the preflight permission report. Do not upload the
operator's profile directory to EC2.

## 2. Establish the retained foundation and DNS

Use a globally unique private state bucket in the partner account. The
[bootstrap module](terraform/README.md) creates the bucket with encryption,
versioning, public access blocking, TLS enforcement and learner-role denial.
Its own backend is local. Preserve that private bootstrap state and its backup;
never run bootstrap again against an existing bucket without reconciling state.
The bucket and DNS namespace outlive an individual activity.

For a delegated namespace, follow [partner DNS](terraform/partner-dns/README.md).
Keep its backend separate from activity keys. Supply these private input fields:
`aws_profile`, `expected_account_id`, `region`, `owner_id`, `partner_id` and
`parent_domain`. Review the saved create-only plan, apply that exact file and
export the delegation:

```bash
terraform -chdir=workshop/aws/terraform/partner-dns output -json delegation
```

Send the exported `domain`, `hosted_zone_id` and actual four `name_servers` to the
parent administrator through the agreed channel. Verify authoritative parent
and child answers and public resolution before requesting certificates. A zone
creation receipt alone does not prove delegation. If using your own domain,
perform the equivalent delegation in your own DNS administration.

Each namespace needs its own apex and wildcard certificate. For example,
`*.workshop.example.com` does not cover
`class-a-s001-jupyter.partner-a.workshop.example.com`.

## 3. Build and qualify each private image

Prepare a private base-image event using [the configuration example](examples/event.example.yaml)
and [pilot settings](examples/pilot.example.yaml). Replace every fictional
account, profile, domain, AMI and expiry. Select an exact available Canonical
Ubuntu 24.04 amd64 AMI, the reviewed full course SHA, one validation learner,
`m7i.2xlarge`, 200 GiB and no spare. Select only the intended profiles. Settings
must name your state bucket and the new bundle directory.

```bash
.venv-workshop/bin/python tools/workshop_config.py validate --config "$IMAGE_EVENT"
.venv-workshop/bin/python tools/workshop_image.py prepare \
  --config "$IMAGE_EVENT" --output "$IMAGE_BUNDLE"
.venv-workshop/bin/python tools/workshop_image.py plan \
  --config "$IMAGE_EVENT" --settings "$IMAGE_SETTINGS" \
  --build-id "$BUILD_ID" --run-dir "$BUILD_RUN"
```

Inspect the private preflight and saved Terraform plan, including expected
account, exact source, hashes, expiry, budget and resource ownership. Start the
build within the 15-minute review window:

```bash
.venv-workshop/bin/python tools/workshop_image.py build --run-dir "$BUILD_RUN"
```

The [Packer guide](packer/README.md) describes sealing, failed-build recovery
and precise build-resource cleanup. A successful build creates a candidate,
not an activity release. Keep its runtime bundle, manifest, candidate and exact
image/snapshot IDs.

Follow [fresh-image validation](image-validation.md) on a different instance:

```bash
.venv-workshop/bin/python tools/workshop_validation.py plan \
  --config "$VALIDATION_EVENT" --settings "$IMAGE_SETTINGS" \
  --candidate-run "$BUILD_RUN" --routes "$VALIDATION_ROUTES" \
  --run-dir "$VALIDATION_RUN"
# Inspect the fresh saved plan before applying it.
.venv-workshop/bin/python tools/workshop_validation.py apply --run-dir "$VALIDATION_RUN"
.venv-workshop/bin/python tools/workshop_validation.py audit --run-dir "$VALIDATION_RUN"
.venv-workshop/bin/python tools/workshop_validation.py course --run-dir "$VALIDATION_RUN"
```

Use the same source/profile selection and candidate, with a new event/expiry and
routes for every selected browser service. On this audited instance, actually
read a lesson, run/save/download a Notebook, restart/reconnect its kernel, submit
a quiz, use the terminal and operate the selected FE/BI/Flink or Observability
interfaces. Keep truthful browser evidence and the complete course report.
A JSON field set to `true` cannot replace the browser interaction.

Destroy and reconcile the validation environment, then publish:

```bash
.venv-workshop/bin/python tools/workshop_validation.py destroy --run-dir "$VALIDATION_RUN"
.venv-workshop/bin/python tools/workshop_validation.py publish \
  --run-dir "$VALIDATION_RUN" --course-report "$COURSE_REPORT" \
  --browser-report "$BROWSER_REPORT" --release-ref "$RELEASE_ID" \
  --output "$RELEASE_FILE"
```

Publication binds the exact private, encrypted partner-owned image to its
runtime and audit/course/browser/cleanup evidence. Retain the private records
and runtime bundle. Repeat the process for another source/profile selection;
never relabel an old image as a newly validated version.

## 4. Operate and hand over activities

Use [the organizer runbook](organizer-guide.md) to create the shared entry,
independent activities and learner accounts from the qualified releases. Give
each learner [the learner guide](learner-guide.md), its own activity URL and its
own credential through your chosen private channel. The tools suppress Cognito
messages and do not send email/chat invitations automatically.

The partner handoff contains version/profile/release references, domain and
zone/delegation outputs, the private state/backend inventory, operating and
cleanup instructions, measured validation scope, cost observations, support
contact and image retention dates. Private state and credentials stay with the
partner, separate from a public source release.

Before the first event, the partner verifies real TLS/login/ownership, course
execution, kernel and terminal WebSockets, cross-learner rejection, logout scope,
service recovery and cleanup in its own account. Our same-account validation
does not certify a partner's external account; that full external exercise
belongs to final delivery acceptance.

## Exit and reference material

The partner removes its activities and shared entry using exact owned states,
retires unneeded private images/snapshots and confirms retained costs. For a
company namespace, the parent administrator separately withdraws the agreed
NS delegation and optional activity-list entry. Removing DNS does not stop EC2
or delete snapshots. Retire the retained zone/foundation only through their own
reviewed procedure after resolving remaining records and state ownership.

AWS references: [local login](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-sign-in.html),
[subdomain delegation](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/dns-routing-traffic-for-subdomains.html),
[certificate names](https://docs.aws.amazon.com/acm/latest/userguide/acm-certificate-characteristics.html).
