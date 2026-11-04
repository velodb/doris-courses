# Local UI integration

The concise English A design is the implementation baseline. The public activity entry, assigned-service page, course index, lesson reader, terminal guide, help and portal signed-out page now use the independently maintained server-rendered UI.

Authentication, subject assignment, service origins and resource lifecycle stay in the existing adapters. No browser script, learning-record store or score/certificate service was added. Cognito remains hosted; its branding and real cloud verification are not completed by this local integration.

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
