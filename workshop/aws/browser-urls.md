# Browser URLs and internal routes

Python, curl, SQL clients and Docker retain their host-side addresses. In
particular, `lab.fe_http_url` remains a loopback address, and container DNS names
remain container DNS names. `lab.fe_browser_url` and `lab.show_browser_link(...)`
use `doris_labkit.browser.browser_url` to produce a browser entry. The helper
maps parsed loopback URLs by exact port; it never replaces text in code, SQL,
Markdown or HTTP bodies. Query strings, fragments and paths survive mapping.
Relative content links retain the normal local behavior.

Without `DORIS_BROWSER_ROUTES_FILE`, browser links keep their original local
URLs. For SSM-only access, create a separate port forward for each web service
and map its host port to the corresponding local browser URL. For example,
write a local `ports.json` with `{"18030": "http://127.0.0.1:28030"}`, then run:

```bash
.venv-workshop/bin/python tools/workshop_urls.py \
  --config workshop/aws/events/event.local.yaml --learner learner-001 \
  --ports workshop/aws/events/ports.json \
  --output workshop/aws/.local/learner-001-browser-routes.json
```

The learner must exist in the event configuration. Deliver that learner's
non-secret route file to the instance or local kernel environment, and set
`DORIS_BROWSER_ROUTES_FILE`, `DORIS_WORKSHOP_EVENT_ID` and
`DORIS_WORKSHOP_LEARNER_ID` for the Jupyter service before starting kernels.
The helper rejects a mismatched event/learner and an unmapped loopback web port
when routes are configured. HTTPS service base URLs can include a path prefix;
the file records the assignment rather than inferring it from array position.

The route file provisions no proxy or authentication. The [Cognito gateway](cognito-access.md)
enforces the same ownership independently for every request. Applications
with root-relative assets, absolute redirects, cookies and WebSockets require
service-specific proxy configuration. `browser_redirect` resolves a Location
header relative to its internal request URL and applies the same mapping;
it does not intercept HTTP requests or rewrite cookies automatically. Separate
SSM forwards retain the service's root path and need no response rewriting.

## Browser validation still required

| Surface | Internal address | Browser integration checks |
| --- | --- | --- |
| Jupyter / course reader | 127.0.0.1:8888 | Login, base prefix, Notebook/terminal sockets, refresh, reconnect and download |
| Single FE / Lab 2 QueryProfile | `DORIS_RTA_HTTP_PORT` (18030) | FE login, Profile view, redirect, assets and query parameters |
| Module 9 FE 1–3 | `DORIS_RTA_M09_FE1_HTTP_PORT` through FE3 (18130–18132) | Map each selected FE separately, including overridden ports and leader redirects |
| Optional Metabase | `DORIS_RTA_METABASE_PORT` (13000) | Opt-in only; setup/login, cookie path/SameSite, assets, dashboard navigation and saved state |

The Module 9 SQL proxy, Kafka, Stream Load and BE HTTP endpoints are internal
Lab traffic. They are not browser entries. Test their host/container behavior
without substituting a browser hostname. The CLI and helper have offline tests;
this does not certify a shared reverse proxy or the cloud browser experience.

## Named services and dynamic ports

Shared access adds an optional `services` map from a stable service name, such as
`dw-flink`, to its protected browser base URL. `browser_url(..., service="dw-flink")`
uses this origin when that map is present. A missing named route is rejected.
Existing port-only SSM files and local mode retain their previous behavior.

Flink discovers the current host port through Docker Compose. It keeps the
internal `REST` URL on loopback and calls `register_browser_port` to update an
activity/learner-bound local registry atomically. The gateway reads the registry
for each request, so changing the dynamic port does not change the browser origin
or require a gateway restart. Missing or mismatched registry entries return 503.
See [service integration](service-access.md) for application settings and scope.
