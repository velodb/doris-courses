"""ALB-signed identity and ownership checks before loopback HTTP/WebSocket proxying."""

import argparse
import asyncio
from collections import OrderedDict
from contextlib import AsyncExitStack
from datetime import datetime, timezone
import hashlib
import importlib.util
from functools import lru_cache
from http.cookies import SimpleCookie
import json
import logging
from logging.handlers import SysLogHandler
import math
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlencode, urlsplit, urlunsplit

from aiohttp import ClientError, ClientSession, ClientTimeout, DummyCookieJar, WSMsgType, web
import jwt
from multidict import CIMultiDict
from yarl import URL

UI_ROOT = Path(__file__).resolve().parent
if not (UI_ROOT / 'i18n.py').exists():
    nested = UI_ROOT / 'ui'
    UI_ROOT = nested if (nested / 'i18n.py').is_file() else Path(__file__).resolve().parents[3] / 'ui/workshop'
sys.path.insert(0, str(UI_ROOT))
from i18n import InstalledUI, LocaleUnavailable

HOP_HEADERS = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
               "te", "trailer", "transfer-encoding", "upgrade", "content-length"}
IDENTITY_HEADERS = {"forwarded", "remote-user", "x-remote-user", "x-auth-user", "x-auth-request-user"}
ID = re.compile(r"[a-z][a-z0-9-]{0,31}")
HOST = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}")
KID = re.compile(r"[a-fA-F0-9]{8}(?:-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12}")
SESSION = web.AppKey("session", ClientSession)
VERIFIER = web.AppKey("verifier", object)
LOG = logging.getLogger('workshop.connection')


def configure_diagnostics(address=Path('/dev/log')):
    """Send only our fixed metadata events to syslog; keep process output muted."""
    if not address.is_socket():
        return None
    handler = SysLogHandler(address=str(address))
    handler.setFormatter(logging.Formatter('doris-workshop-connection: %(message)s'))
    LOG.addHandler(handler)
    LOG.setLevel(logging.WARNING)
    LOG.propagate = False
    return handler


class Denied(ValueError):
    def __init__(self, status=401):
        self.status = status


def validate_config(config):
    """No deployment credentials, client secrets or TLS keys belong on the host."""
    required = {"schema_version", "event_id", "learner_id", "region", "alb_arn", "issuer",
                "client_id", "subjects", "expires_at", "services", "login_origin"}
    version = config.get('schema_version')
    if version == 2:
        required.add('ui_snapshot')
    if type(version) is not int or version not in (1, 2) or set(config) != required:
        raise ValueError("Invalid gateway configuration fields")
    if version == 2:
        InstalledUI.from_snapshot(config['ui_snapshot'])
    if not all(isinstance(config[k], str) and ID.fullmatch(config[k]) for k in ("event_id", "learner_id")):
        raise ValueError("Invalid event or learner")
    if not re.fullmatch(r"[a-z]{2}-[a-z-]+-\d", config["region"]):
        raise ValueError("Invalid commercial AWS Region")
    if not re.fullmatch(r"arn:aws:elasticloadbalancing:" + re.escape(config["region"]) + r":\d{12}:loadbalancer/app/[a-zA-Z0-9-]+/[a-f0-9]{16}", config["alb_arn"]):
        raise ValueError("Invalid ALB ARN")
    if (not isinstance(config["issuer"], str) or not config["issuer"].startswith(
            f'https://cognito-idp.{config["region"]}.amazonaws.com/') or
            not re.fullmatch(r"[a-zA-Z0-9]+", config["client_id"])):
        raise ValueError("Expected this Region's Cognito issuer and app client")
    login = urlsplit(config["login_origin"])
    if login.scheme != "https" or not re.fullmatch(r"[a-z0-9-]+\.auth\." + re.escape(config["region"]) + r"\.amazoncognito\.com", login.netloc) or login.path or login.query or login.fragment:
        raise ValueError("Invalid Cognito login origin")
    if (not isinstance(config["subjects"], list) or not config["subjects"] or
            any(not isinstance(s, str) or not s or len(s) > 256 for s in config["subjects"])):
        raise ValueError("Assign explicit immutable Cognito subjects")
    expiry = datetime.strptime(config["expires_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    if not isinstance(config["services"], dict) or not config["services"]:
        raise ValueError("Configure at least one service")
    ports = set()
    for host, service in config["services"].items():
        if (not isinstance(host, str) or not HOST.fullmatch(host) or
                not {"port", "jupyter", "redirect_origins"} <= set(service) or
                set(service) - {"port", "jupyter", "redirect_origins", "dynamic_port", "relay_group"} or
                ("dynamic_port" in service and service["dynamic_port"] != "dw-flink") or
                ("relay_group" in service and (service["relay_group"] != "rta-m09-fe" or
                                               service["jupyter"] or "dynamic_port" in service)) or
                type(service["port"]) is not int or not 1024 <= service["port"] <= 65535 or
                service["port"] == 8080 or type(service["jupyter"]) is not bool or
                not isinstance(service["redirect_origins"], list)):
            raise ValueError("Invalid explicit loopback service route")
        if service["port"] in ports:
            raise ValueError("Each published loopback port needs a unique origin")
        ports.add(service["port"])
        for origin in service["redirect_origins"]:
            parsed = urlsplit(origin)
            if (parsed.scheme != "http" or not parsed.hostname or parsed.path or parsed.query or parsed.fragment or
                    parsed.username is not None or parsed.password is not None):
                raise ValueError("Redirect aliases must be explicit internal HTTP origins")
    return expiry


class Verifier:
    def __init__(self, config, session):
        self.config, self.session = config, session
        self.keys = OrderedDict()
        self.last_fetch = 0.0
        self.lock = asyncio.Lock()

    async def key(self, kid):
        if not isinstance(kid, str) or not KID.fullmatch(kid):
            raise Denied()
        async with self.lock:
            cached = self.keys.get(kid)
            if cached and cached[0] > time.monotonic():
                return cached[1]
            # Bound unknown-kid requests; neither jku nor a client URL is used.
            if time.monotonic() - self.last_fetch < 1:
                raise Denied()
            self.last_fetch = time.monotonic()
            url = f'https://public-keys.auth.elb.{self.config["region"]}.amazonaws.com/{kid}'
            try:
                async with self.session.get(url, allow_redirects=False, timeout=ClientTimeout(total=5)) as response:
                    if response.status != 200:
                        raise Denied()
                    value = await response.content.read(8193)
                    if len(value) > 8192 or not value.startswith(b"-----BEGIN PUBLIC KEY-----"):
                        raise Denied()
            except Denied:
                raise
            except Exception:
                raise Denied() from None
            self.keys[kid] = (time.monotonic() + 3600, value)
            while len(self.keys) > 16:
                self.keys.popitem(last=False)
            return value

    async def verify_claims(self, token):
        if not isinstance(token, str) or len(token) > 16384:
            raise Denied()
        try:
            header = jwt.get_unverified_header(token)
            if (header.get("alg") != "ES256" or header.get("signer") != self.config["alb_arn"] or
                    header.get("iss") != self.config["issuer"] or header.get("client") != self.config["client_id"]):
                raise Denied()
            key = await self.key(header.get("kid"))
            claims = jwt.decode(token, key, algorithms=["ES256"], options={"verify_aud": False})
            # ALB puts these fields in its SIGNED HEADER, not in the userinfo payload.
            expiry = header.get("exp")
            if isinstance(expiry, str) and expiry.isascii() and expiry.isdigit():
                expiry = int(expiry)
            if type(expiry) not in (int, float) or not math.isfinite(expiry) or expiry <= time.time():
                raise Denied()
            return claims, expiry
        except Denied:
            raise
        except (jwt.PyJWTError, ValueError, TypeError):
            raise Denied() from None

    async def verify(self, token):
        claims, expiry = await self.verify_claims(token)
        if claims.get("sub") not in self.config["subjects"]:
            raise Denied(403)
        return expiry


def clean_headers(headers):
    connection = {s.strip().lower() for s in headers.get("Connection", "").split(",")}
    return [(k, v) for k, v in headers.items() if k.lower() not in HOP_HEADERS | connection | IDENTITY_HEADERS
            and not k.lower().startswith(("x-amzn-", "x-forwarded-", "sec-websocket-"))]


def redirect(location, config, resolved_ports=None):
    parsed = urlsplit(location)
    if not parsed.netloc:
        return location
    # Doris can embed HTTP Basic credentials in a follower-to-leader Location.
    # Match only the configured authority and never expose userinfo to a browser.
    authority = parsed.netloc.rsplit("@", 1)[-1]
    for host, service in config["services"].items():
        port = (resolved_ports or {}).get(host, service["port"])
        aliases = [f'http://127.0.0.1:{port}', f'http://localhost:{port}',
                   *service["redirect_origins"]]
        if f"{parsed.scheme}://{authority}" in aliases:
            return urlunsplit(("https", host, parsed.path, parsed.query, parsed.fragment))
    if "@" in parsed.netloc:
        raise Denied(502)
    # Unknown internal redirects must not send learners to their own localhost.
    if parsed.scheme == "http":
        raise Denied(502)
    return location


def secure_cookies(headers):
    result = []
    for value in headers.getall("Set-Cookie", []):
        jar = SimpleCookie()
        jar.load(value)
        if not jar:
            raise Denied(502)
        for morsel in jar.values():
            morsel["domain"] = ""
            morsel["secure"] = True
            if not morsel["samesite"]:
                morsel["samesite"] = "Lax"
            result.append(morsel.OutputString())
    return result


def resolve_port(service, config, registry_file):
    if "dynamic_port" not in service:
        return service["port"]
    try:
        registry = json.loads(Path(registry_file).read_text())
        if (registry["schema_version"] != 1 or registry["event_id"] != config["event_id"] or
                registry["learner_id"] != config["learner_id"]):
            raise Denied(503)
        port = registry["ports"][service["dynamic_port"]]
        if type(port) is not int or not 1024 <= port <= 65535 or port == 8080:
            raise Denied(503)
        return port
    except (OSError, ValueError, KeyError, TypeError):
        raise Denied(503) from None


@lru_cache(maxsize=1)
def guidance_renderer():
    path = Path(__file__).with_name('service_guidance.py')
    if not path.is_file():
        path = Path(__file__).resolve().parents[3] / 'ui/workshop/service_guidance.py'
    spec = importlib.util.spec_from_file_location('gateway_service_guidance', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.unavailable_page


def unavailable_response(request, config, host, authorized, context=None):
    headers = {'Cache-Control': 'no-store', 'Retry-After': '5', 'X-Content-Type-Options': 'nosniff'}
    if (authorized and request.method in {'GET', 'HEAD'} and 'text/html' in request.headers.get('Accept', '')
            and request.headers.get('Upgrade', '').lower() != 'websocket'
            and request.path != '/api' and not request.path.startswith('/api/')):
        prefix = config['event_id'] + '-' + config['learner_id'] + '-'
        service_id = host.split('.', 1)[0].removeprefix(prefix)
        domain = host.split('.', 1)[1]
        workspace = next(('https://' + name + '/workshop/' for name, service in config['services'].items() if service['jupyter']), None)
        assets = 'https://' + domain + '/doris-courses/static'
        html = guidance_renderer()(service_id, origin='https://' + host, workspace=workspace, assets=assets, context=context)
        headers['Content-Security-Policy'] = ("default-src 'none'; style-src https://" + domain +
            '; img-src https://' + domain + '; font-src https://' + domain +
            "; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
        return web.Response(status=503, text=html, content_type='text/html', headers=headers)
    return web.Response(status=503, text='Service unavailable. Start the course service and retry.\n', headers=headers)


def application(config, *, verifier=None, token_file=Path("/run/doris-workshop/token"),
                registry_file=Path("/run/doris-workshop/service-ports.json"), installed_ui=None):
    activity_expiry = validate_config(config)
    installed_ui = installed_ui or (InstalledUI.from_snapshot(config['ui_snapshot']) if config['schema_version'] == 2 else InstalledUI())
    app = web.Application(client_max_size=64 * 1024**2)

    async def session_context(app):
        async with ClientSession(cookie_jar=DummyCookieJar(), trust_env=False,
                                 auto_decompress=False, timeout=ClientTimeout(total=3600)) as session:
            app[SESSION] = session
            app[VERIFIER] = verifier or Verifier(config, session)
            yield
    app.cleanup_ctx.append(session_context)

    async def handle(request):
        host, authorized = None, False
        try:
            if request.path == "/healthz" and request.method in {"GET", "HEAD"}:
                return web.Response(status=200 if time.time() < activity_expiry else 503, text="gateway\n")
            if len(request.headers.getall("Host", [])) != 1:
                raise Denied(400)
            host = request.headers["Host"]
            service = config["services"].get(host)
            if service is None or time.time() >= activity_expiry:
                if service is not None:
                    return web.Response(status=403, text='Workshop has ended.\n',
                        headers={'Cache-Control': 'no-store', 'X-Workshop-Access': 'expired'})
                raise Denied(403)
            # This landing page must not trigger a fresh ALB login after logout.
            if request.path == "/_workshop/logged-out":
                try:
                    context = installed_ui.context(explicit=request.query.get('locale'), browser=request.headers.get('Accept-Language', ''))
                except LocaleUnavailable:
                    context = installed_ui.context(explicit='en')
                return web.Response(text=context.messages('recovery.signed-out') + "\n", headers={"Cache-Control": "no-store", "Content-Language": context.locale})
            if len(request.headers.getall("X-Amzn-Oidc-Data", [])) != 1:
                raise Denied()
            expiry = min(activity_expiry, await app[VERIFIER].verify(request.headers["X-Amzn-Oidc-Data"]))
            authorized = True
            try:
                explicit_locale = request.query.get('locale') if (request.path.startswith('/_workshop/') or
                    (request.method in {'GET', 'HEAD'} and 'text/html' in request.headers.get('Accept', ''))) else None
                if explicit_locale is not None and len(request.query.getall('locale')) != 1:
                    raise Denied(400)
                locale_context = installed_ui.context(explicit=explicit_locale, browser=request.headers.get('Accept-Language', ''),
                    activity=config['event_id'], learner=config['learner_id'], scope=host)
            except LocaleUnavailable:
                raise Denied(406)
            websocket = request.headers.get("Upgrade", "").lower() == "websocket"
            if request.method not in {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}:
                raise Denied(405)
            if websocket or request.method not in {"GET", "HEAD", "OPTIONS"}:
                if request.headers.get("Origin") != "https://" + host:
                    raise Denied(403)
            if service['jupyter'] and request.method in {'GET', 'HEAD'}:
                if request.path == '/_workshop/session':
                    return web.json_response({'expires_at': config['expires_at']}, headers={'Cache-Control': 'no-store'})
                if request.path in {'/_workshop/session_recovery.js', '/_workshop/session_recovery.css'}:
                    name = request.path.rsplit('/', 1)[1]
                    body = Path(__file__).with_name(name).read_bytes()
                    if name.endswith('.js'):
                        # JSON remains JS data, never HTML. No tokens or diagnostics.
                        messages = json.dumps(locale_context.messages.subset('recovery.'), ensure_ascii=True)
                        locale = json.dumps(locale_context.locale)
                        body = ('(() => {const workshopMessages=' + messages + ';const workshopLocale=' + locale + ';\n').encode() + body + b'\n})();'
                    return web.Response(body=body,
                        content_type='text/javascript' if name.endswith('.js') else 'text/css',
                        headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})
                if request.path == '/_workshop/reauth':
                    h = locale_context.messages.html
                    return web.Response(text=f'<!doctype html><html lang="{locale_context.locale}"><head><meta charset="utf-8">'
                        f'<title>{h("recovery.restored-title")}</title><script src="{locale_context.url("/_workshop/session_recovery.js")}" defer></script>'
                        f'</head><body><p>{h("recovery.restored")}</p></body></html>',
                        content_type='text/html', headers={'Cache-Control': 'no-store',
                            'Content-Security-Policy': "default-src 'none'; script-src 'self'; base-uri 'none'; frame-ancestors 'none'"})
            if request.path == "/_workshop/logout":
                if request.method != "POST":
                    raise Denied(405)
                target = config["login_origin"] + "/logout?" + urlencode({"client_id": config["client_id"], "logout_uri": f"https://{host}/_workshop/logged-out"})
                response = web.Response(status=303, headers={"Location": target, "Cache-Control": "no-store"})
                cookie = "DorisAuth" + hashlib.sha256(host.encode()).hexdigest()[:20]
                for suffix in ("", "-0", "-1", "-2", "-3"):
                    response.del_cookie(cookie + suffix, path="/", secure=True, httponly=True)
                response.del_cookie("AWSALBAuthNonce", path="/", secure=True, httponly=True)
                return response
            headers = CIMultiDict(clean_headers(request.headers))
            headers.update({"Host": host, "X-Forwarded-Host": host, "X-Forwarded-Proto": "https", "X-Forwarded-Port": "443"})
            cookies = SimpleCookie()
            cookies.load(headers.pop("Cookie", ""))
            safe = [m.OutputString() for key, m in cookies.items() if not key.startswith(("DorisAuth", "AWSALB"))]
            if safe:
                headers["Cookie"] = "; ".join(safe)
            if service["jupyter"]:
                headers["Authorization"] = "token " + Path(token_file).read_text().strip()
            lab_document = (service['jupyter'] and request.method == 'GET' and
                (request.path == '/lab' or request.path.startswith('/lab/')) and
                'text/html' in request.headers.get('Accept', ''))
            if lab_document:
                headers['Accept-Encoding'] = 'identity'
                # A pre-overlay cached page must not bypass bootstrap injection
                # through an upstream 304 after the learner refreshes the tab.
                headers.pop('If-None-Match', None)
                headers.pop('If-Modified-Since', None)
            port = resolve_port(service, config, registry_file)
            target = URL(f'http://127.0.0.1:{port}{request.raw_path}', encoded=True)
            if websocket:
                protocols = tuple(s.strip() for s in request.headers.get("Sec-WebSocket-Protocol", "").split(",") if s.strip())
                async with app[SESSION].ws_connect(target, headers=headers, protocols=protocols,
                                                      autoclose=True, heartbeat=30, max_msg_size=64 * 1024**2) as upstream:
                    downstream = web.WebSocketResponse(protocols=[upstream.protocol] if upstream.protocol else (),
                        heartbeat=30, max_msg_size=64 * 1024**2)
                    await downstream.prepare(request)

                    async def pump(source, destination):
                        async for message in source:
                            if time.time() >= expiry:
                                break
                            if message.type == WSMsgType.TEXT:
                                await destination.send_str(message.data)
                            elif message.type == WSMsgType.BINARY:
                                await destination.send_bytes(message.data)
                            elif message.type in {WSMsgType.CLOSE, WSMsgType.ERROR}:
                                break

                    tasks = [asyncio.create_task(pump(upstream, downstream)), asyncio.create_task(pump(downstream, upstream))]
                    try:
                        await asyncio.wait(tasks, timeout=max(0, expiry - time.time()), return_when=asyncio.FIRST_COMPLETED)
                    finally:
                        for task in tasks:
                            task.cancel()
                        await asyncio.gather(*tasks, return_exceptions=True)
                        expired = time.time() >= expiry
                        ended = time.time() >= activity_expiry
                        reason = 'workshop_expired' if ended else 'identity_expired' if expired else 'peer_closed'
                        LOG.warning('workshop_socket_closed reason=%s service=%s', reason,
                                    'jupyter' if service['jupyter'] else 'course')
                        await downstream.close(code=4003 if ended else 4001 if expired else 1000,
                            message=reason.encode())
                    return downstream
            # A browser cannot follow a cross-origin 307 with Authorization and an
            # unsafe Origin. Only opted-in FEs of this learner may relay such a
            # request internally; buffer once so the body can be replayed exactly.
            relay_group = service.get("relay_group")
            body = (await request.read() if relay_group and request.can_read_body else
                    request.content if request.can_read_body else None)
            async with AsyncExitStack() as stack:
                upstream = await stack.enter_async_context(app[SESSION].request(
                    request.method, target, headers=headers, data=body, allow_redirects=False))
                original_cookies = None
                if relay_group and request.method not in {"GET", "HEAD", "OPTIONS"} and upstream.status in {307, 308} and "Location" in upstream.headers:
                    mapped = urlsplit(redirect(upstream.headers["Location"], config, {host: port}))
                    destination = config["services"].get(mapped.netloc)
                    if (mapped.scheme == "https" and destination and mapped.netloc != host and
                            destination.get("relay_group") == relay_group):
                        if time.time() >= expiry:
                            raise Denied()
                        original_cookies = secure_cookies(upstream.headers)
                        path = urlunsplit(("", "", mapped.path or "/", mapped.query, ""))
                        next_target = URL(f'http://127.0.0.1:{resolve_port(destination, config, registry_file)}{path}', encoded=True)
                        upstream = await stack.enter_async_context(app[SESSION].request(
                            request.method, next_target, headers=headers, data=body, allow_redirects=False))
                outgoing = [(k, v) for k, v in clean_headers(upstream.headers) if k.lower() not in {"set-cookie", "location"}]
                if lab_document and upstream.status == 200 and upstream.content_type == 'text/html':
                    document = await upstream.read()
                    if (not upstream.headers.get('Content-Encoding') and len(document) < 2 * 1024**2
                            and b'</head>' in document):
                        script_url = locale_context.url('/_workshop/session_recovery.js') if 'locale' in request.query else '/_workshop/session_recovery.js'
                        injection = '<link rel="stylesheet" href="/_workshop/session_recovery.css">' + f'<script src="{script_url}"></script></head>'
                        document = document.replace(b'</head>', injection.encode(), 1)
                        outgoing = [(k, v) for k, v in outgoing if k.lower() not in {'etag', 'content-md5', 'cache-control'}]
                        outgoing.append(('Cache-Control', 'no-store'))
                    response = web.Response(status=upstream.status, body=document, headers=outgoing)
                    response.headers['Strict-Transport-Security'] = 'max-age=31536000'
                    response.headers['X-Content-Type-Options'] = 'nosniff'
                    for value in secure_cookies(upstream.headers):
                        response.headers.add('Set-Cookie', value)
                    return response
                response = web.StreamResponse(status=upstream.status, headers=outgoing)
                response.headers["Strict-Transport-Security"] = "max-age=31536000"
                response.headers["X-Content-Type-Options"] = "nosniff"
                if "Location" in upstream.headers:
                    response.headers["Location"] = redirect(upstream.headers["Location"], config, {host: port})
                # Cookies from the leader belong to its own public Host. Keep
                # only the follower's cookies when responding on the follower.
                for value in (original_cookies if original_cookies is not None else secure_cookies(upstream.headers)):
                    response.headers.add("Set-Cookie", value)
                await response.prepare(request)
                async for chunk in upstream.content.iter_chunked(65536):
                    if time.time() >= expiry:
                        break
                    await response.write(chunk)
                await response.write_eof()
                return response
        except Denied as error:
            LOG.warning('workshop_access_denied status=%s', error.status)
            if error.status == 503:
                return unavailable_response(request, config, host, authorized, locals().get('locale_context'))
            return web.Response(status=error.status, text="Workshop access denied.\n", headers={"Cache-Control": "no-store"})
        except (OSError, asyncio.TimeoutError, ClientError):
            LOG.warning('workshop_upstream_unavailable')
            return unavailable_response(request, config, host, authorized, locals().get('locale_context'))

    app.router.add_route("*", "/{tail:.*}", handle)
    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    configure_diagnostics()
    app = application(json.loads(args.config.read_text()))
    web.run_app(app, host=args.bind, port=args.port, access_log=None, print=None)


if __name__ == "__main__":
    main()
