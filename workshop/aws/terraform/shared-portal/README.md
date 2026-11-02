# Operator portal host

This separate state provisions one operator-controlled Ubuntu 24.04 amd64
EC2 host, an encrypted 20 GiB gp3 root disk, an instance profile and an
SSM-only role. It uses the existing shared-access VPC, subnet and dedicated
portal security group. It creates no inbound rules, SSH key, learner runtime,
Docker socket, Cognito identity or state-storage permission.

Supply explicit account/profile/Region/owner/access IDs, a verified official
Canonical AMI, subnet/VPC and shared portal/ALB group IDs in private variables.
The module checks VPC ownership, AMI provenance and actual security-group
rules: only the selected ALB group may enter TCP 8080. A public IPv4 address
provides outbound HTTPS without a NAT gateway. Bootstrap uses HTTPS Ubuntu
repositories to install Python's venv support; operator SSM installs the
separate, hash-bound portal overlay afterwards.

The default is `t3.small` with Standard CPU credits; `t3.medium` is also
supported. These hosts can throttle after consuming their credit balance,
without unlimited-mode surplus-credit charges. They serve the lightweight
activity index and assignment links. Learner computation runs elsewhere.
IMDSv2 is required with hop limit 1. SSH and detailed monitoring are disabled.

Use a new encrypted, versioned S3 backend key with S3 lockfiles, independent
of shared access and activities. Refresh identity, offerings, AMI, quotas,
prices and the approved test window; save and review the exact plan before
applying it. Export `terraform output -json portal`, then pass its instance
ID as `portal_instance_id` in a fresh shared-access plan. The root route opens
only after that separate attachment.

The [portal runtime](../../access/portal.py) supports an empty `activities`
map before the first activity is registered. The root index reports that no
activities are available and rejects unregistered activity hosts. Global
domain, Region and ALB validation still apply. No placeholder Cognito pool
or subject is needed to bootstrap the index.

Install through [shared HTTPS access](../../shared-access.md#operator-owned-course-entry).
Verify SSM, bootstrap, HTTPS, exact-host/path rejection, ALB health and service
restart separately. A target health check proves only the portal process.
Provider mocks do not certify those cloud checks.

This is shared infrastructure and remains billable until explicitly retired.
Activity expiry and activity destroy do not remove this state. To retire it,
detach its listener route/target before destroying the host, disk, role and
profile with a separately reviewed plan. Retain the partner DNS zone and
company delegation independently.
