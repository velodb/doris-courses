# Local UI integration

The concise English A design is the implementation baseline. The public activity entry, assigned-service page, course index, lesson reader, terminal guide, help and portal signed-out page now use the independently maintained server-rendered UI.

Authentication, subject assignment, service origins and resource lifecycle stay in the existing adapters. No browser script, learning-record store or score/certificate service was added. Cognito remains hosted; its branding and real cloud verification are not completed by this local integration.


## Candidate validation

The private candidate built from `db2620cb51c29a5fb6577e242dadeb03c15c368e` passed 27 fresh-instance audit checks and all ten RTA Labs plus ten quizzes (189 code cells). A clean native Linux x86_64 source tree at the same revision passed 389 operator/runtime tests, five UI tests and 591 shared/course tests (589 passed, two DOG-specific skips). The full run exposed a startup race in Module 9: heartbeat readiness preceded BE disk reports. The candidate includes a bounded storage-report wait that also supports a registered, stopped third BE.

The shared portal's real HTTPS entry, four fixed assets, unknown-path rejection and limited asset CORS passed HTTP checks. GUI acceptance remains pending because window control returned `noWindowsAvailable` and the browser connection could not load its request-header policy. The candidate remains unpublished; the new three-learner authenticated UI round and native GitHub Actions result remain pending. These checks do not promote the earlier published image or the separate TTL runner.

The original local integration checks were:

| Check | Result |
| --- | --- |
| Complete operator/runtime tool suite on local Python 3.12 | 388 passed |
| UI presentation/package boundary tests | 5 passed |
| Terraform mocks for access bindings and shared access | 16 + 9 passed |
| Browser screenshots of real local portal/Jupyter handlers | 15 checked; no page/CSP/overflow errors |
| Installer syntax, embedded Python and public links | Passed |
| SSM UI staging / main command JSON | 19,516 / 16,585 bytes; each below 24,000 |

Browser verification uses fixture-signed OIDC claims and a temporary local Jupyter token. It does not verify AWS ALB/Cognito, IAM, real account isolation or deployment. Fixtures bind only loopback and remove their processes and credentials after the run.

Runtime bundles contain a hash-bound `ui.tar`; the portal stages its UI separately and downloads two public assets by pinned SHA256 during installation. Every dispatch retains instance/input binding and refuses to replay an unknown result. Existing AMIs, runner images, bindings and saved plans were not changed or reused.

Use [architecture.md](architecture.md) for maintenance and reproduction. The next deployment must build a new runtime/image, preserve the current preflight and reviewed-plan process, and validate the UI with actual authentication, services, isolation, logout and cleanup.
