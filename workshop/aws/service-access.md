# Course service access

The [Cognito access workflow](cognito-access.md) publishes a separate protected
origin for each selected service and learner. All origins use the same signed
identity and ownership checks, including APIs and WebSockets. Containers continue
to publish only to loopback. Fixed application passwords do not bypass ALB or
the gateway.

| Profile | Browser services | Application setting |
| --- | --- | --- |
| Every profile | Jupyter reader, notebooks and terminal | Loopback Jupyter through the authenticated gateway |
| `rta-main` | Single FE and three Module 9 FEs | Parsed Location redirects; explicit container origins map to the correct FE |
| `rta-metabase` | Metabase | `DORIS_RTA_METABASE_PUBLIC_URL` becomes `MB_SITE_URL` |
| `dw-main` | FE QueryProfile | `profile_url()` produces a browser URL; profile-text HTTP stays internal |
| `dw-bi` | Superset | `DW_BI_PUBLIC_URL` enables trusted proxy headers; API/CSRF clients stay on loopback |
| `dw-cdc` | Flink | Named browser origin with a locally discovered dynamic port |
| `obs-main` | FE, Grafana, demo, Mailpit, Litefuse | Separate browser properties and public application URL settings |
| `ai-mcp` / `ai-functions` | FE | FE route only; no browser MCP Host or public model endpoint is added |

The route generator includes only services belonging to selected profiles. It
does not start the services. For two learners with all listed profiles there
are 30 origins and 90 listener rules. This is a local count, not a live quota
check or permission to deploy those courses. The current validated release
contains `rta-main`; other profiles still require their own compatible runtime,
image and course acceptance.

## Public URLs and internal clients

The generated per-learner environment sets browser-facing URLs for Grafana,
demo, Litefuse, Metabase and Superset, and applies explicit fixed-port overrides
to the corresponding course environment variables. The overlay installer loads
this file through Jupyter's systemd environment. Restart Jupyter and recreate
affected containers when changing their environment. Keep application data
volumes. Metabase refuses to reuse a container with a different configured
public site URL rather than silently keeping stale settings.

Grafana's `root_url` uses its HTTPS browser origin. The demo's browser-side OTLP
exporter sends to that learner's demo origin; the Collector still uses Docker
network addresses. Litefuse's `NEXTAUTH_URL` uses its HTTPS origin while its
SDK, Doris, Silo and Redis traffic retain internal endpoints. Superset trusts
the canonical proxy headers only when public access is configured. Its loopback
API keeps its existing CSRF/session behavior; the gateway upgrades browser
cookies to Secure and removes Domain attributes.

`lab.grafana_url`, `lab.demo_url`, `lab.mailpit_url`, `lab.litefuse_url` and
`superset_url()` remain internal API addresses. Use the corresponding
`*_browser_url` properties/functions for browser navigation. No broad string
replacement is performed in SQL, Python, HTTP bodies or notebook content.

## Dynamic Flink ports and FE redirects

`streaming.configure_streaming_port()` discovers `jobmanager:8081` through
`docker compose port`, updates its internal REST URL and writes this learner's
port registry when workshop mode is configured. The browser continues to use
the stable `dw-flink` origin. Each gateway request reads the current registry;
missing entries, wrong ownership, invalid ports and stopped services return 503.
Restarting Flink and rerunning port discovery updates the target without
restarting the gateway. Local mode does not create a registry.

The default Module 9 redirect aliases match the course's explicit
`172.31.91.40` through `.42` FE addresses on port 8030. If those internal origins
change, pass a private `--redirects` file to `workshop_access.py routes`, for
example `{"s001": {"rta-fe1": ["http://172.31.91.40:8030"]}}`. The parser maps
only explicit origins to their assigned service host and preserves path, query
and fragment. Unknown HTTP redirect origins fail closed. Response bodies and
host-side Stream Load redirects are not rewritten by this browser proxy.

## Acceptance boundary

Local tests cover named browser routes, unchanged internal API endpoints,
per-learner environment generation, missing/stale dynamic registration, live
port switching, stopped-service errors, deep redirects, cookies and real
Jupyter kernel/terminal sockets. Docker Compose configuration was rendered with
both local defaults and public URLs without starting containers.

Real browser login, static resources, deep links, Grafana Live, Superset charts,
Metabase dashboards, Litefuse callbacks and browser-generated demo spans must
still be verified through the formal domain on EC2 for each selected profile.
Do not treat configuration rendering, a health check or an API result as that
browser acceptance. Public TLS and Cognito acceptance remain pending too.
