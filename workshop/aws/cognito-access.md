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
3. Initialize a separate private activity-access backend and apply its reviewed
   saved plan. Export `terraform output -json identity`. Authentication rules
   are installed from the beginning; without a running gateway they return a
   backend error and do not expose a service.
4. Create the accounts with the explicitly configured AWS profile:

   ```bash
   python tools/workshop_accounts.py \
     --config workshop/aws/events/event.local.yaml \
     --identity workshop/aws/.local/identity.json \
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
     --identity workshop/aws/.local/identity.json \
     --subjects workshop/aws/.local/account-delivery/subjects.json \
     --output-dir workshop/aws/.local/gateways
   python tools/workshop_access.py bundle \
     --output-dir workshop/aws/.local/access-overlay
   ```

6. Upload the overlay and that learner's gateway JSON through operator SSM.
   Run `bash install_gateway.sh /path/to/access-overlay /path/to/learner.json /path/to/learner-browser.json /path/to/learner.env`
   as root on the already verified prebuilt host. The installer checks activity,
   learner, expiry and overlay hashes. It installs an independent gateway venv
   and Jupyter launch wrapper; it does not modify the fixed AMI or its original
   runtime files. Preserve `overlay.json` alongside the release record. This
   overlay installation still requires Linux/EC2 acceptance.
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

Applications retain their own login where required. Each service gets its own
origin and host-only Secure cookies. The outer ALB session never grants a user
another learner's service. No progress, grades, certificate or learning analytics
service is created.

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

Local tests cover cryptographic rejection, ownership, HTTP forwarding, Cookie and
redirect handling, WebSocket text/binary/subprotocols, expiry, real Jupyter reader,
file save, kernel execution and terminal sockets. They do not certify real Cognito
login, public TLS, account recovery or EC2/browser network isolation.

References: [ALB signed claims and logout](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/listener-authenticate-users.html),
[Cognito user pools](https://docs.aws.amazon.com/cognito/latest/developerguide/cognito-user-pools.html).
