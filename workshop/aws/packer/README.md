# Private workshop AMI builds

This pipeline prepares a private **candidate** AMI for `rta-main`, or `dw-main`
with its selected optional profiles, on Ubuntu
24.04 amd64, using one `m7i.2xlarge` and one encrypted 200 GiB gp3 root volume.
It is separate from the learner activity and its Terraform state. Building an
image does not certify a fresh instance or publish the image for activities.
Independent launch, identity, course and browser validation must follow.

The complete [data-warehousing selection](../data-warehousing.md#current-acceptance)
has passed independent EC2 course/browser validation and private publication.
Its final shared-browser acceptance remains pending cleanup and reconciliation.
RTA evidence below applies only to its recorded RTA versions.

Two independent real builds in us-east-1 on 2026-10-30 UTC completed runtime
installation, cache preparation, sealing and private AMI creation. Their full
version manifests matched, including Python freezes, system package versions
and container content IDs. Build-time checks do not replace the independent
new-instance course and browser validation required to release a candidate.

## Fixed inputs

Use an explicit Canonical AMI ID owned by `099720109477`, a full course commit,
and a clean committed deployment checkout. The runtime bundle contains the
widgets frontend and gzip changes. `image-input.json` records the course and
deployment revisions and runtime/helper hashes, without account login profiles,
learner IDs, browser domains or event credentials.

`locks/` contains the resolved Python 3.12/Linux x86_64 dependencies and artifact
hashes. The installer uses wheel-only `pip --require-hashes --no-deps`, followed
by the pinned source's local editable packages with dependency resolution and
build isolation disabled. `setuptools` and `wheel` are included in the locks.
The source requirements, project metadata and runtime package list are hashed
in `locks/metadata.json`; changed inputs require reviewed lock regeneration.
Installed freezes remain at `/var/lib/doris-workshop/installed-packages/` and
their hashes appear in `/etc/doris-workshop/image-manifest.json`.

Data Warehousing uses `locks/doris-dw.in`, `doris-dw.requirements.lock` and
`metadata-dw.json`, including the PyArrow dependency for its extensions. The
resolver cutoff and platform are recorded in that metadata. Only the selected
course kernel and server locks enter its bundle.

To regenerate both locks, use the resolver and flags recorded in metadata:

```bash
uv pip compile workshop/aws/packer/locks/server.in \
  --python-version 3.12 --python-platform x86_64-manylinux_2_39 \
  --generate-hashes --only-binary :all: --default-index https://pypi.org/simple \
  --no-annotate --no-header -o workshop/aws/packer/locks/server.requirements.lock
uv pip compile workshop/aws/packer/locks/doris-rta.in \
  --python-version 3.12 --python-platform x86_64-manylinux_2_39 \
  --generate-hashes --only-binary :all: --default-index https://pypi.org/simple \
  --no-annotate --no-header -o workshop/aws/packer/locks/doris-rta.requirements.lock
```

Review `.in` files against the selected course's requirements and both project
metadata files, then refresh `metadata.json` hashes. Do not update hashes alone
to bypass a changed dependency input. OS APT repositories can change; this is a
version-audited build, not a promise of byte-identical future AMIs. Docker's five
APT packages are pinned by the runtime package list.

`images.json` pins the three required container digests against the selected
commit's image table. The builder pulls Linux amd64 digests and tags them with
the course's existing tags. Both `/var/lib/docker` and `/var/lib/containerd` are
on the root snapshot. No separate cache disk can be lost at capture time.
No Lab containers or volumes are created. This profile needs no extra model,
plugin, or custom container build. Optional Metabase and other profiles are
excluded.

For Data Warehousing, `images-dw.json` selects the lake images required by its
main path, Kafka for `dw-kafka`, MySQL/Flink for `dw-cdc`, and Superset for
`dw-bi`. Custom Flink/Superset images are built from the exact course commit
with the base image digest fixed. Their content IDs, source-context hashes,
and installed Python or connector inventories enter the image manifest. The
builder runs only temporary inventory containers, removes them and verifies
that no containers or data volumes remain before sealing. Debian/PyPI/Maven
inputs for these custom builds can change; independent builds are audited,
without a claim of byte-identical rebuilds.

The seal step stops Docker and removes its engine ID while preserving the image
cache, so a newly built image can generate a separate ID on each host. This
change does not modify previously published AMIs; a new build and independent
validation are required for those images.

The public events Parquet dataset remains an online S3 dependency, preserving
the TVF/loading lessons. Lab 3 downloads its small public CSV/JSONL fixtures
when taught. The image includes no loaded tables. AWS SSM endpoints are also
required. APT, Docker registry and PyPI are needed during image construction.
Doris startup also contacts Docker Hub for the course's explicit `docker pull`,
even when its image layers are already cached.

## Prepare, review and build

Install the [preflight dependencies](../preflight-requirements.txt), Terraform
1.17.1, Packer 1.16.2, and the pinned Amazon plugin 1.8.3. Verify official
release checksums when installing binaries. Use the established explicit AWS
profile. No credentials are passed in Packer variables or uploaded to EC2.
Run from the repository root and use absolute paths for private run files:

```bash
python tools/workshop_image.py prepare \
  --config workshop/aws/events/image-build.yaml \
  --output workshop/aws/.local/image-bundle-01
python tools/workshop_image.py plan \
  --config workshop/aws/events/image-build.yaml \
  --settings workshop/aws/events/image-build-settings.yaml \
  --build-id rta-image-01 \
  --run-dir workshop/aws/.local/image-run-01
```

The event config uses the existing base-image schema. Select `rta-main`,
`m7i.2xlarge`, 200 GiB, no spares, and a fresh expiry within 24 hours. Settings
use the existing pilot schema and reference the **new image bundle**. The
existing learner-count cost/quota estimate is conservative for one builder;
the image plan adds a full 200 GiB snapshot retained for seven days and checks
the approved budget again. An estimate is not a billing cap.

Read the private `preflight.json` and Terraform plan. The saved plan is bound
to the bundle, variables, module and tooling. Build within its 15 minute
preflight window, using a new run if it expires or any input changes:

```bash
python tools/workshop_image.py build --run-dir workshop/aws/.local/image-run-01
```

Terraform creates a private build-input bucket, zero-inbound network, SSM
installation document and a dedicated `doris-workshop-builder-*` role/profile.
The role reads only this build's input objects and SSM control channels. It
cannot read Terraform state, create instances, change IAM, or fetch model keys.
Packer runs with `communicator = "none"`; a local provisioner discovers exactly
one build-tagged instance and invokes the document through SSM. No SSH session or inbound rule is used. The Amazon plugin may still create a
tagged temporary EC2 key pair; normal Packer cleanup and the fallback cleanup
remove it. Private key material is never uploaded to the runtime. The build host's expiry timer stops an overdue
builder; full Terraform cleanup remains the operator's responsibility.

SSM can report a later document step as missing while earlier installation
steps are still running. The provisioner retries that response and checks the
whole command for failures. A completed command whose preparation step stays
missing fails after a short consistency grace period.

After installation and runtime checks, the pipeline prepares container caches,
records actual installed versions and exports a compact host manifest through
SSM. The sealing command stops Jupyter, Docker, containerd and the SSM agent;
removes homes, tokens, cookies, shell history, AWS/Docker login files, SSH keys,
SSM state, cloud-init state, machine identity and logs; and recreates only
pristine selected Notebook copies. It disables the build-only expiry timer,
flushes the filesystem and stops the host. The local provisioner waits for the
stopped state before Packer may capture it. A sealing failure cannot pass by
waiting a fixed number of seconds.

The AMI has `/etc/doris-workshop/image-sealed` and a `candidate` manifest. The
normal Jupyter service creates fresh credentials on next boot. A new launch
must verify clean machine/SSM identity, new credentials and pristine Lab state
before releasing this image. No event address or learner binding is baked in.

## Retention, failure and cleanup

Successful builds write private `packer-manifest.json`, `image-manifest.json`
and `candidate.json`. Candidate validation checks the AMI is available,
private, has no launch-sharing grants, and contains exactly one encrypted,
account-owned completed root snapshot. Preserve the AMI and snapshot IDs for
the independent new-instance validation task.

Canonical's AMI metadata can also contain virtual instance-store mappings.
The selected m7i.2xlarge is EBS-only; these inherited hints do not add an EBS
cache disk. Validation counts actual EBS mappings, accepts virtual-device
hints, and still rejects an additional EBS volume or an unknown mapping.
AMI build/version tags must match the run's fixed manifest.

If an operator-side check or connection fails after Packer has created the
artifact, repeat the checks using its preserved run directory:

```bash
python tools/workshop_image.py verify --run-dir workshop/aws/.local/image-run-01
```

Verification queries AWS again. An identical existing candidate record is
retained; a conflicting record is refused for review.

The build command runs cleanup in a `finally` block. It terminates only live
instances with this build's ID and scope tags, waits for termination, then
destroys the build Terraform resources. For an interrupted process, retry:

```bash
python tools/workshop_image.py cleanup --run-dir workshop/aws/.local/image-run-01
```

Keep the run directory/state until reconciliation is complete. Check empty
Terraform state and absence of this build's instances, EBS/ENIs, network,
bucket, SSM document, temporary EC2 key pair and IAM role/profile. Do not remove another activity or
the foundation state bucket. Failed Packer builds can leave candidate AMIs or
snapshots; inventory the build tags and retain or explicitly delete each one.
Cleanup deliberately retains images and snapshots for independent validation.

AMI metadata has no recurring charge; the retained EBS snapshot incurs storage
charges. The preflight stores a live price quote and conservatively estimates
all 200 GiB, even though snapshot billing depends on stored data. Review the
candidate after seven days. `ExpiresAt` tags are inventory hints and do not
schedule image or snapshot deletion. After a failed validation or retirement,
deregister the exact AMI and delete its now-unreferenced snapshots. Record the
API result and any Recycle Bin retention; never infer deletion from a tag.

References: [Packer Amazon EBS builder](https://developer.hashicorp.com/packer/integrations/hashicorp/amazon/latest/components/builder/ebs),
[cloud-init image cleaning](https://docs.cloud-init.io/en/latest/reference/cli.html),
[AWS AMI lifecycle](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/AMIs.html).
