# Cognito learner access

Use one activity-owned Cognito user pool with administrator-created accounts.
Public registration is disabled. Temporary passwords expire after one day and
must be changed at first login. TOTP MFA is available; account recovery is handled
by the operator. No IAM Identity Center or learner AWS credentials are needed.

The `terraform/access-bindings` state owns this pool, confidential ALB app client,
Cognito domain, per-learner target groups, listener rules and exact DNS aliases.
It receives the [shared access output](shared-access.md); it does not own the
shared network, ALB, certificate, DNS zone or existing enterprise users. Its
client secret stays in the operator's private Terraform state. No client secret
or TLS private key is sent to the learner host.

## Prepare and deploy

Keep all real inputs and generated files under ignored `events/` or `.local/`
directories, with operator-only permissions. Install
`workshop/aws/preflight-requirements.txt` in the operator environment.

1. Deploy shared access and fixed-release learner hosts as described in
   [shared HTTPS access](shared-access.md). Save the direct JSON values of
   `terraform output -json access` and `terraform output -json instance_ids`.
2. Prepare binding variables and browser route metadata without making AWS calls:

   ```bash
   python tools/workshop_access.py routes \
     --config workshop/aws/events/event.local.yaml \
     --access workshop/aws/.local/access.json \
     --instances workshop/aws/.local/instances.json \
     --priority-base 1000 --priority-limit 1999 \
     --allocations workshop/aws/.local/listener-allocations.json \
     --init-allocations --output workshop/aws/.local/access-plan.json
   ```

   `--init-allocations` is only for the first activity on a new listener. All
   later generations and activities reuse the same private allocation file and
   omit that flag. Each activity reserves a distinct inclusive interval; changing
   its owner or interval is rejected. Withdrawn hosts retain their slots. See
   [stable batch operations](batch-operations.md) for updates and locking.
   Reserve an unused listener priority range for this activity. Each service
   consumes three priorities: logout landing, API and page. The generated
   activity entry consumes another three when shared access has an operator
   portal, and the shared root list consumes one. Keep its reserved priority
   outside every activity range. See the [operator portal](shared-access.md#operator-owned-course-entry)
   for the separate host and its private assignment configuration. The generated
   `access-plan.tfvars.json` is input to `terraform/access-bindings`. Existing
   ALB rules count toward its quota, including rules from other activities.
   Live quota, callback count, identity, ownership and cost checks are required
   before saving and reviewing the real plan. The local generator cannot query
   other activities. A rule cap is not evidence of account-wide free capacity.
   For [separate DNS accounts](shared-access.md#separate-dns-account), add
   `--dns workshop/aws/.local/dns.json`. That private file contains the `dns`
   object's fields directly, for example
   `{"account_id":"111111111111","profile":"workshop-dns"}`. Its account must
   equal `access.dns_account_id`; the route generator preserves it in the
   binding variables while keeping the workload profile/account unchanged.
   A matching `role_arn` can replace `profile`. Neither passwords nor access
   keys belong in this file. Cross-account output without this explicit DNS
   identity is rejected. Keep the same inputs available for activity destroy.
3. Prepare a private activity-access directory containing `inputs.tfvars.json`
   copied from the generated `access-plan.tfvars.json` and
   `backend.tfbackend.json` for the same state bucket, profile, account and
   Region. Its key must be
   `aws-workshop/aws/ACCOUNT/REGION/EVENT/access.tfstate`, with encryption and
   the S3 lockfile enabled. Create the initial bindings through a reviewed plan:

   ```bash
   python tools/workshop_bindings_create.py plan \
     --pilot-run workshop/aws/.local/event-run \
     --bindings-run workshop/aws/.local/event-bindings \
     --route workshop/aws/.local/access-plan.json \
     --output workshop/aws/.local/bindings-create-review
   # Inspect the saved plan and review.json, then use its printed receipt hash.
   python tools/workshop_bindings_create.py apply \
     --pilot-run workshop/aws/.local/event-run \
     --bindings-run workshop/aws/.local/event-bindings \
     --route workshop/aws/.local/access-plan.json \
     --output workshop/aws/.local/bindings-create-review \
     --approved-review-sha256 REVIEWED_RECEIPT_SHA256
   ```

   The command checks the bound pilot and shared entry, live preflight, DNS and
   listener capacity, exact create-only plan, state lock and activity fence. It
   writes the private identity output to `bindings-create-review/identity.json`.
   For an enrolled TTL revision supply `--ttl-binding` and cancel that revision
   before changing scope. A partial or unknown apply must be reconciled before
   creating another plan. Authentication rules are installed from the beginning;
   without a running gateway they return a backend error and expose no service.
4. Create the accounts with the explicitly configured AWS profile:

   ```bash
   python tools/workshop_accounts.py \
     --config workshop/aws/events/event.local.yaml \
     --identity workshop/aws/.local/bindings-create-review/identity.json \
     --output-dir workshop/aws/.local/account-delivery
   ```

   This command mutates the owned pool. It verifies the caller account and pool
   tags, suppresses Cognito messages, stores passwords in private delivery files
   and writes the immutable `sub` mapping separately. It never resets an existing
   account. Retain per-user creation receipts after a partial failure; reconcile
   those exact users before retrying. Deliver credentials through your chosen
   private channel. This command sends no email or chat messages.
5. Generate only the assigned subjects and routes for each host:

   ```bash
   python tools/workshop_access.py gateways \
     --plan workshop/aws/.local/access-plan.json \
     --identity workshop/aws/.local/bindings-create-review/identity.json \
     --subjects workshop/aws/.local/account-delivery/subjects.json \
     --output-dir workshop/aws/.local/gateways
   python tools/workshop_access.py bundle \
     --output-dir workshop/aws/.local/access-overlay
   ```

6. Install the fixed checked-in overlay through the guarded operator command:

   ```bash
   python tools/workshop_gateway_deploy.py \
     --pilot-run workshop/aws/.local/event-run \
     --plan workshop/aws/.local/access-plan.json \
     --identity workshop/aws/.local/bindings-create-review/identity.json \
     --subjects workshop/aws/.local/account-delivery/subjects.json
   ```

   It checks the bound source, shared ALB, current instance assignments, pool
   ownership and exact Cognito subjects before SSM dispatch. Use `--learner ID`
   to install one assigned host. Installation records are retained in the
   private pilot lifecycle and carried into roster updates. A pending command is
   polled, an unknown dispatch is not resent, and a failed installation needs
   explicit `--retry-failed` with at most three attempts. Enrolled TTL revisions
   must be cancelled before rollout and supplied with `--ttl-binding`.

   Recovery assets and the installer use separate bounded SSM stages, each with
   its own durable dispatch receipt. The installer verifies the complete overlay
   manifest, including the previously staged assets, before installing anything.

   The installer checks activity, learner, expiry and overlay hashes. It installs
   an independent gateway venv and Jupyter launch wrapper; it does not modify
   the fixed AMI. Gateway installation is not authenticated browser acceptance.
   The lower-level installer remains available for separately reviewed SSM
   workflows; keep its files and any diagnostics private.
7. The installer copies that learner's generated browser JSON and public URL
   environment to `/etc/doris-workshop/`. The prebuilt Jupyter activity
   environment already selects this route file. Restart Jupyter after changing
   service environment settings; restart the gateway after changing its routes
   or assignments. Check course readiness independently of `/healthz`.

## Trust and session boundary

Every page, API request and WebSocket handshake verifies `x-amzn-oidc-data` with
ES256 and an AWS ALB public key. Key IDs must be UUIDs; key URLs are constructed
from the configured Region, cached with a fixed bound and fetched without
redirects. The verifier checks the signed header's ALB ARN, issuer, client and
expiry, then checks the payload's subject against this host's assignment.
Unsigned identity/access-token headers cannot authorize a request.

Unknown hosts, expired activities, wrong subjects and invalid signatures are
denied before contacting a backend. WebSockets and writes require the exact
HTTPS Origin. Sockets close when the signed identity or activity expires.
The gateway strips ALB tokens, session cookies and client proxy identity headers
before forwarding. Jupyter retains its own rotating local token and XSRF
configuration; the gateway authenticates to loopback Jupyter with that token.
The Jupyter wrapper accepts the gateway's canonical proxy headers while keeping
the original loopback-only listener.

The gateway adds a small recovery script before JupyterLab starts. A same-origin
session check every 30 seconds lets ALB refresh its identity claims. WebSocket
ping/pong keeps idle network connections responsive; sockets still close at the
signed identity or activity expiry. JupyterLab reconnects using a fresh signed
handshake without restarting the Python kernel.

If an API read encounters an authentication denial or login redirect, the script
keeps that read pending and offers **Sign in to reconnect**. The button opens a
separate sign-in window, preserving the notebook page and unsaved edits. After
the authenticated session check succeeds, only GET/HEAD requests resume. Writes,
kernel starts/restarts and cell messages are never replayed by the recovery
script. Aborted reads remain abortable. HTTP 404 still represents a missing
kernel rather than a refreshable login. Genuine network failures retain their
network error handling. Activity expiry has a separate terminal message and
cannot be extended through re-login.

Gateway syslog diagnostics record close reasons and denial status only; they
do not record credentials, request query strings, notebook content or cell
messages. General process stdout/stderr remain muted by the operations policy;
the gateway sends only its fixed metadata messages to the local syslog socket.
A gateway-only rollout preserves Jupyter and kernel processes, but
existing browser tabs must save their work and refresh once to load the recovery
script. The normal full installer also configures the Jupyter launch wrapper and
therefore restarts Jupyter; do not use it for a live gateway-only patch.

Applications retain their own login where required. Each service gets its own
origin and host-only Secure cookies. The outer ALB session never grants a user
another learner's service. New source runtimes can store [personal progress](runtime/progress.md)
on that learner's instance. Authentication does not create a central learning,
certificate or analytics service, or synchronize records between activities.

Send a same-origin POST to `/_workshop/logout` to clear this service's ALB cookie
and its shards, then visit Cognito's logout endpoint. The exact registered
`/_workshop/logged-out` landing returns a static response without authentication.
Logout applies to this browser origin. Other service tabs have their own ALB
cookies and existing sockets can remain until their signed expiry. Close those
tabs or revoke the assignment and restart gateways for immediate activity access
removal; Cognito account deletion alone does not instantly invalidate cached ALB
sessions. No global logout guarantee is claimed.

Destroy activity-access state before the learner hosts. It removes only the
activity's dedicated Cognito pool/accounts and routing resources. Existing
enterprise identity systems and shared infrastructure remain. Reconcile resources
and remove private credential-delivery files after the activity retention period.

Local tests cover cryptographic rejection, ownership, HTTP forwarding, cookies,
redirects and WebSockets. Two-learner events also exercised real Cognito login,
public TLS, browser and API isolation, notebook execution, terminal WebSockets,
entry logout and re-login. A real ALB-signed identity was rejected after natural
expiry and its idle WebSocket closed; a separate 3600-second ALB authentication
session also expired while a later session remained usable. These checks cover
the tested events and do not certify account recovery or every future deployment.

The recovery browser acceptance uses a real local JupyterLab and gateway with
simulated ALB authentication; it does not require or change an AWS account:

```bash
PYTHONPATH=tools/tests python tools/tests/session_recovery_fixture.py \
  --output workshop/aws/.local/recovery-acceptance --soak-seconds 3700
```

Install the runtime test requirements and Playwright first, and set
`PLAYWRIGHT_MODULE` to its installed module path. The fixture checks identity
expiry during execution, authentication denial and redirects, a separate sign-in
window, two browsers on one notebook, process/variable/edit preservation, write
non-replay, missing kernels and workshop expiry. Its optional soak crosses
15-minute signed-identity and 3600-second authentication-session boundaries.
These local simulations supplement the earlier cloud checks; they do not certify
the recovery behavior of a future live ALB deployment by themselves.

References: [ALB signed claims and logout](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/listener-authenticate-users.html),
[Cognito user pools](https://docs.aws.amazon.com/cognito/latest/developerguide/cognito-user-pools.html).
