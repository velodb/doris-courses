# Start an activity from a validated private image

Select an exact AMI ID, owner account, Region, architecture, course SHA,
profile set and `release_ref` in the event's `image` object. Use
`image.mode: prebuilt` and the [prebuilt example](examples/prebuilt.example.yaml)
for its shape. There is no latest-image lookup or automatic upgrade.

For a partner account, the partner follows the [self-build runbook](partner-guide.md)
using its own credentials to build, validate and publish the release. No company
image or KMS sharing permission is required. This activity flow requires
the AMI owner to match the deployment account and forbids sharing grants.

The pilot settings additionally require `image_release_file`, pointing to the
immutable private JSON produced after [fresh-image validation](image-validation.md).
The release binds the AMI and encrypted root snapshot to the runtime, image
manifest and audit/course/browser/cleanup evidence hashes. Keep the matching
`runtime_bundle_dir` locally for hash verification; prebuilt startup uploads no
installer objects and performs no package installation. `browser_routes_file`
optionally maps each assigned learner to their own host-port/browser-URL map.
For RTA, map host FE ports `18030` and `18130`–`18132`; container port `8030`
is not the host entry. Endpoints still need SSM forwards or a protected gateway.

Use the existing activity lifecycle:

```bash
python tools/workshop.py plan --config workshop/aws/events/event.yaml \
  --pilot workshop/aws/events/pilot.yaml --run-dir workshop/aws/.local/activity
# Review the saved plan and its SHA256, then:
python tools/workshop.py up --run-dir workshop/aws/.local/activity \
  --approved-plan-sha256 REVIEWED_SHA256
python tools/workshop.py up --run-dir workshop/aws/.local/activity
python tools/workshop.py status --run-dir workshop/aws/.local/activity
```

Offline and live preflight reject mismatched account, Region, architecture,
course, profiles, root size, runtime or release hashes. Live checks also reject
candidate/retired/public/shared AMIs. Terraform repeats the private-image and
release-tag checks. User data injects activity identity/routes and starts the
baked services. The CLI uses a bounded SSM wait for cloud-init and learner Docker access
before the image-verification document checks initialization and runtime
readiness. Startup command IDs are retained and reused. The first `up` records apply timestamps and elapsed readiness;
repeated `up` keeps instances and installation command IDs, then runs a fresh
health probe. The readiness scope covers the host and course entry; full Lab
validation belongs to the image release evidence.

To replace one learner, first produce a private replacement plan:

```bash
python tools/workshop.py replace-plan --run-dir workshop/aws/.local/activity --learner s001
# Read replacement.json and review replace.tfplan. The selected learner's
# temporary disk data will be deleted; there is no automatic restore.
python tools/workshop.py replace --run-dir workshop/aws/.local/activity \
  --approved-plan-sha256 REVIEWED_REPLACEMENT_SHA256
```

The plan must replace exactly that learner's instance. Changes to shared
resources or other learners, incomplete or deferred plans, and duplicate or
nested managed resource addresses are rejected. Apply requires the same assignments,
inputs, plan hash and a fresh 15-minute plan window, repeats live preflight,
and verifies the resulting assignments and readiness. Failed operations retain
instance/volume IDs and private diagnostics for `status` and `destroy`.
Readiness timing and before/after IDs are retained in `lifecycle.json`.
The old activity remains pinned even when a separate release is published;
choose a new version explicitly for a new activity.

This flow covers the pilot host only. If a learner is also connected to a
shared entry, its separate access state still targets the old instance and the
new host does not have the gateway overlay. Reconcile the learner's target
attachment with a fresh reviewed access plan, install its own gateway and
verify authenticated access separately. Host readiness does not establish
that the shared entry is ready. The guarded pilot CLI does not orchestrate
these steps.

After validation or an event, destroy the activity and reconcile its owned
resources. Retain the original foundation state and release records:

```bash
python tools/workshop.py destroy --run-dir workshop/aws/.local/activity --approve-destroy
```

The AMI and snapshot have a separate retention policy and continue to cost
money until explicitly deregistered/deleted. Expiry tags do not remove them.

Live validation used two `m7i.2xlarge` hosts in `us-east-1`, each with a
200 GiB encrypted root, pinned to the same independently validated `rta-main`
release. The first dual-host check exposed a transient Docker readiness failure;
the CLI reported failure and retained both instances for diagnosis. A bounded
Docker startup wait was added before verification, and the same activity
recovered. Repeated `up` took about 22 seconds with unchanged instance and
startup/verification command IDs. A stopped Jupyter service was detected in
about 24 seconds while both EC2s remained running; recovery kept both instances.
A cold replacement of one learner then became ready in about 158 seconds on its
first startup/verification attempts. The other learner's instance, root volume,
machine identity, token and test file remained unchanged. These are observed
integration timings, including polling; they are not a startup SLA. A separate
unbound catalog fixture left the original activity's saved inputs unchanged,
and a real Terraform replan reported all 16 managed resources as no-op.

The validation activity was destroyed, a repeated destroy succeeded, and AWS
API checks found no remaining owned EC2/EBS, network/ENI, IAM, SSM document or
runtime bucket resources. The original foundation and one validated image with
its encrypted snapshot were retained separately.
