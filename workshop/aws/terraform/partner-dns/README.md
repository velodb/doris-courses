# Partner DNS namespace

This module creates one public Route 53 zone in the partner's explicitly
selected AWS account. Its name is `<partner_id>.<parent_domain>`. Route 53
creates its authoritative NS and SOA; the module exports the actual four NS
and an NS-record handoff for the parent-domain administrator. It does not
modify the parent zone or require company credentials.

Use a separate encrypted, versioned S3 state key with normal S3 lockfiles,
for example `aws-workshop/partner-dns/partner-a/terraform.tfstate`. Supply an
explicit operator profile, expected account, Region, owner, partner ID and
approved parent domain in private variables. Check caller identity, existing
zones, permissions, account limits and current DNS pricing before saving a
plan. Review the saved plan and apply that exact plan. If a zone already
exists, reconcile its ownership and state instead of creating a duplicate.

```bash
terraform -chdir=workshop/aws/terraform/partner-dns init \
  -backend-config=/path/to/private/backend.json -lockfile=readonly
terraform -chdir=workshop/aws/terraform/partner-dns plan \
  -var-file=/path/to/private/inputs.tfvars.json \
  -out=/path/to/private/create.tfplan
terraform -chdir=workshop/aws/terraform/partner-dns apply \
  /path/to/private/create.tfplan
terraform -chdir=workshop/aws/terraform/partner-dns output -json delegation
```

The company administrator adds the exported NS record in the parent zone.
Verify parent and child authoritative answers and public resolution before
using the namespace. Creating a zone alone does not establish delegation.
The partner can then use the zone's ID and domain with
[shared HTTPS access](../../shared-access.md), keeping DNS, ACM, ALB, Cognito
and learner resources in its own account. The same partner zone can support
multiple activities without repeating company delegation.

This namespace outlives individual activities. It has no Event or ExpiresAt
tag, `force_destroy = false` and `prevent_destroy = true`. Its state is not
an activity or TTL cleanup target. Retiring it requires a separate reviewed
change, coordinating parent delegation and remaining records first. Preserve
state and namespace outputs for subsequent deployments. A retained zone
continues to incur DNS charges; consult
[Route 53 pricing](https://aws.amazon.com/route53/pricing/).

The module does not register a domain, configure DNSSEC or create a certificate,
portal, ALB or activity. These remain separately owned resources. Public-zone
creation and delegation follow the
[Route 53 subdomain procedure](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/dns-routing-traffic-for-subdomains.html).
