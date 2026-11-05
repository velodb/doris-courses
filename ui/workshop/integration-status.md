# Workshop UI integration and validation

The concise English A design is the implementation baseline. The public activity entry, assigned-service page, course index, lesson reader, terminal guide, help and portal signed-out page now use the independently maintained server-rendered UI.

Authentication, subject assignment, service origins and resource lifecycle stay in the existing adapters. No browser script, learning-record store or score/certificate service was added. Cognito remains hosted; its branding is unchanged. Real cloud verification is recorded separately below.


## Private-image validation

The private image built from `db2620cb51c29a5fb6577e242dadeb03c15c368e` passed 27 fresh-instance audit checks and all ten RTA Labs plus ten quizzes (189 code cells). A clean native Linux x86_64 source tree at the same revision passed 389 operator/runtime tests, five UI tests and 591 shared/course tests (589 passed, two DOG-specific skips). The full run exposed a startup race in Module 9: heartbeat readiness preceded BE disk reports. The image includes a bounded storage-report wait that also supports a registered, stopped third BE.

On a separate fresh instance, the same image passed all 20 Notebooks again and six actual browser checks: reader/images, Notebook create/run/save/download, kernel restart/reconnect, quiz navigation/feedback, terminal Docker access and an FE query-profile detail. Its validation resources were destroyed and reconciled before the private release `rta-db2620c-ui-v1` was published. Browser evidence is bound to that same audited instance and immutable image source.

The new shared portal's real HTTPS entry, four fixed assets, unknown-path rejection and limited asset CORS passed HTTP checks. Three clones have distinct Docker engine, machine and token identities, and all three completed concurrent Lab 1. Three separate browser sessions passed Cognito sign-in, their own five service origins, Notebook SQL returning 42, terminal Docker access and FE login. All six directed cross-learner pairs returned HTTP 403 for five page/API paths and failed both kernel and terminal WebSocket handshakes. Owner WebSocket controls succeeded; the failed handshakes did not expose an HTTP status to the browser test.

All three accounts reached the branded signed-out page, required Cognito reauthentication and returned to the correct assignment. Entry logout left other service-origin sessions usable, matching the documented boundary. Stopping only the third learner's Jupyter produced 503 there while the other two retained their original kernel IDs, processes and terminals. Restarting the service restored SQL and Docker access without replacing an EC2 instance. The round used administratively prepared disposable passwords; it did not test first-login password changes or hosted-login branding.

The operator-only signed-out-route review fix at `ab02729` passed 391 local tools tests and seven focused native Linux x86_64 tests. These native checks do not establish a remote GitHub Actions result. The separate TTL runner keeps its own security-review requirements.

The original local integration checks were:

| Check | Result |
| --- | --- |
| Complete operator/runtime tool suite on local Python 3.12 | 388 passed |
| UI presentation/package boundary tests | 5 passed |
| Terraform mocks for access bindings and shared access | 16 + 9 passed |
| Browser screenshots of real local portal/Jupyter handlers | 15 checked; no page/CSP/overflow errors |
| Installer syntax, embedded Python and public links | Passed |
| SSM UI staging / main command JSON | 19,516 / 16,585 bytes; each below 24,000 |

The original local browser verification used fixture-signed OIDC claims and a temporary local Jupyter token. Those local checks do not verify AWS ALB/Cognito, IAM, real account isolation or deployment. Fixtures bind only loopback and remove their processes and credentials after the run.

Runtime bundles contain a hash-bound `ui.tar`; the portal stages its UI separately and downloads two public assets by pinned SHA256 during installation. Every dispatch retains instance/input binding and refuses to replay an unknown result. Deployment uses the verified release and fresh event plans; payload changes require new artifacts.

Use [architecture.md](architecture.md) for maintenance and reproduction. A UI payload change requires a new runtime/image and the same preflight, reviewed-plan and fresh-instance validation process. Authentication, service isolation, logout and cleanup require their own shared-deployment acceptance for each changed scope; local fixtures do not replace those checks.
