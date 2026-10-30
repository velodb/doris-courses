# Validate a private image on a fresh instance

`tools/workshop_validation.py` creates a separate, temporary Terraform
environment for one candidate AMI. Its instance is independent of the Packer
host. The instance receives activity identity and browser routes through user
data, checks the exact image/runtime hashes, and starts existing services.
Startup installs no packages and preserves the image's pristine Lab files.

Use the established AWS profile and approved event sizing. The validation event
uses one learner and a new event ID, a fresh expiry within 24 hours, and a new
run directory. Its base-mode event/settings provide the existing live
identity, budget, quota and foundation checks; `--candidate-run` supplies the
exact private AMI and immutable build manifest to validate. The actual private
AMI is explicit in the Terraform vars and validation preflight record.

```bash
python tools/workshop_validation.py plan \
  --config workshop/aws/events/validation.yaml \
  --settings workshop/aws/events/validation-settings.yaml \
  --candidate-run workshop/aws/.local/image-build-run \
  --routes workshop/aws/events/validation-routes.json \
  --run-dir workshop/aws/.local/image-validation-run
# Review the saved Terraform plan before creating any resources.
python tools/workshop_validation.py apply --run-dir workshop/aws/.local/image-validation-run
python tools/workshop_validation.py audit --run-dir workshop/aws/.local/image-validation-run
```

The routes file maps each learner ID to `{host_port: browser_base_url}`. Provide
HTTPS service entries or operator SSM forwards on loopback HTTP. Routes and
learner identity are injected per activity; they are absent from the image.
The fresh-image audit runs before Labs and checks image/runtime hashes, actual
Python freezes, system packages, Docker caches/root placement, pristine
Notebooks, new machine/cloud-init/SSM identity, new SSH keys and Jupyter
credentials, ownership and runtime readiness. Reports contain token hashes,
never token values.

Then execute the complete selected course and the required real browser
interactions. Installation success and API execution do not certify browser
interaction. Record the AMI, Region, instance type, timings, download boundaries
and any failures. Keep the image a candidate until those checks pass.

Always remove the validation environment after verification or failure:

```bash
python tools/workshop_validation.py destroy --run-dir workshop/aws/.local/image-validation-run
```

Cleanup checks ownership, permits only destroy/no-op actions, and reconciles
state, EC2 and EBS. Keep the private run directory until API reconciliation of
network/IAM and other owned resources is complete. The foundation and candidate
AMI have separate lifecycles; this command retains them. The host expiry timer
is a cost guard, while the operator still destroys the full environment.

For `rta-main`, provide routes for host FE ports `18030` and `18130`–`18132`.
Run the complete 20-Notebook regression after the initial audit:

```bash
python tools/workshop_validation.py course --run-dir workshop/aws/.local/image-validation-run
```

The runner executes the ten Labs and ten quizzes in managed kernels and
performs the course's documented single-node/multi-node transitions. It writes
executed Notebooks separately, preserves source workbooks, and records cell
counts/timings and failures. Real browser checks remain a separate requirement.

After successful audit, course and browser validation **and** full environment
cleanup, publish the exact candidate:

```bash
python tools/workshop_validation.py publish \
  --run-dir workshop/aws/.local/image-validation-run \
  --course-report workshop/aws/.local/image-validation-run/course-report.json \
  --browser-report workshop/aws/.local/browser-report.json \
  --release-ref rta-v1 --output workshop/aws/.local/releases/rta-v1.json
```

The browser report has `kind: doris-workshop-browser-validation`, `passed: true`,
matching `image_id`, `instance_id`, `course_ref`, and a `checks` map whose required keys are `reader-images`,
`notebook-create-run-save-download`, `kernel-restart-reconnect`,
`quiz-navigation-feedback`, `terminal-docker` and `fe-profile`, each `true`
after the corresponding actual browser interaction. Preserve its supporting screenshots/notes
privately. Publication checks all three reports against the audited fresh
instance and requires reconciled state, EC2/EBS, network, IAM and ENIs to be
absent. It hashes the evidence into the strict
[release schema](image-release.schema.json), tags the exact private AMI as
validated, and refuses to overwrite another published version. Keep the release
JSON unchanged. It supplies the explicit release input for subsequent activity startup.

Live acceptance covered a fresh private `rta-main` image on Ubuntu 24.04,
`m7i.2xlarge` with an encrypted 200 GiB root in `us-east-1`: all 27 initial
checks, ten Labs and ten quizzes (189 code cells), plus the required browser
interactions passed. Execution outputs were downloaded before cleanup.
One earlier event omitted host port 18030 and failed the browser-link cell;
that environment was destroyed, the route guard was added, and a fresh event
passed. Jupyter startup is asynchronous: the audit waits for runtime readiness
before reading the per-instance token. Dependency caches still require Docker
Hub manifest/auth access where the course pulls tags, and course datasets
still download from public S3. Shared HTTPS/OIDC and other course profiles are
outside this acceptance scope.
