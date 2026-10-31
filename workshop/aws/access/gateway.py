"""ALB-signed identity and ownership checks before loopback HTTP/WebSocket proxying."""

import argparse
import asyncio
from collections import OrderedDict
from datetime import datetime, timezone
import hashlib
from http.cookies import SimpleCookie
import json
import math
from pathlib import Path
import re
import time
from urllib.parse import urlencode, urlsplit, urlunsplit

from aiohttp import ClientError, ClientSession, ClientTimeout, DummyCookieJar, WSMsgType, web
import jwt
from multidict import CIMultiDict
from yarl import URL

HOP_HEADERS = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
               "te", "trailer", "transfer-encoding", "upgrade", "content-length"}
IDENTITY_HEADERS = {"forwarded", "remote-user", "x-remote-user", "x-auth-user", "x-auth-request-user"}
ID = re.compile(r"[a-z][a-z0-9-]{0,31}")
HOST = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}")
KID = re.compile(r"[a-fA-F0-9]{8}(?:-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12}")
SESSION = web.AppKey("session", ClientSession)
VERIFIER = web.AppKey("verifier", object)


class Denied(ValueError):
    def __init__(self, status=401):
        self.status = status


def validate_config(config):
    """No deployment credentials, client secrets or TLS keys belong on the host."""
    required = {"schema_version", "event_id", "learner_id", "region", "alb_arn", "issuer",
                "client_id", "subjects", "expires_at", "services", "login_origin"}
    if set(config) != required or config["schema_version"] != 1:
        raise ValueError("Invalid gateway configuration fields")
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
                set(service) - {"port", "jupyter", "redirect_origins", "dynamic_port"} or
                ("dynamic_port" in service and service["dynamic_port"] != "dw-flink") or
                type(service["port"]) is not int or not 1024 <= service["port"] <= 65535 or
                service["port"] == 8080 or type(service["jupyter"]) is not bool or
                not isinstance(service["redirect_origins"], list)):
            raise ValueError("Invalid explicit loopback service route")
        if service["port"] in ports:
            raise ValueError("Each published loopback port needs a unique origin")
        ports.add(service["port"])
        for origin in service["redirect_origins"]:
            parsed = urlsplit(origin)
            if parsed.scheme != "http" or not parsed.hostname or parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password:
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
    for host, service in config["services"].items():
        port = (resolved_ports or {}).get(host, service["port"])
        aliases = [f'http://127.0.0.1:{port}', f'http://localhost:{port}',
                   *service["redirect_origins"]]
        if f"{parsed.scheme}://{parsed.netloc}" in aliases:
            return urlunsplit(("https", host, parsed.path, parsed.query, parsed.fragment))
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


def application(config, *, verifier=None, token_file=Path("/run/doris-workshop/token"),
                registry_file=Path("/run/doris-workshop/service-ports.json")):
    activity_expiry = validate_config(config)
    app = web.Application(client_max_size=64 * 1024**2)

    async def session_context(app):
        async with ClientSession(cookie_jar=DummyCookieJar(), trust_env=False,
                                 auto_decompress=False, timeout=ClientTimeout(total=3600)) as session:
            app[SESSION] = session
            app[VERIFIER] = verifier or Verifier(config, session)
            yield
    app.cleanup_ctx.append(session_context)

    async def handle(request):
        try:
            if request.path == "/healthz" and request.method in {"GET", "HEAD"}:
                return web.Response(status=200 if time.time() < activity_expiry else 503, text="gateway\n")
            if len(request.headers.getall("Host", [])) != 1:
                raise Denied(400)
            host = request.headers["Host"]
            service = config["services"].get(host)
            if service is None or time.time() >= activity_expiry:
                raise Denied(403)
            # This landing page must not trigger a fresh ALB login after logout.
            if request.path == "/_workshop/logged-out":
                return web.Response(text="Signed out of this service. Close other course tabs.\n", headers={"Cache-Control": "no-store"})
            if len(request.headers.getall("X-Amzn-Oidc-Data", [])) != 1:
                raise Denied()
            expiry = min(activity_expiry, await app[VERIFIER].verify(request.headers["X-Amzn-Oidc-Data"]))
            websocket = request.headers.get("Upgrade", "").lower() == "websocket"
            if request.method not in {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}:
                raise Denied(405)
            if websocket or request.method not in {"GET", "HEAD", "OPTIONS"}:
                if request.headers.get("Origin") != "https://" + host:
                    raise Denied(403)
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
            port = resolve_port(service, config, registry_file)
            target = URL(f'http://127.0.0.1:{port}{request.raw_path}', encoded=True)
            if websocket:
                protocols = tuple(s.strip() for s in request.headers.get("Sec-WebSocket-Protocol", "").split(",") if s.strip())
                async with app[SESSION].ws_connect(target, headers=headers, protocols=protocols,
                                                      autoclose=True, max_msg_size=64 * 1024**2) as upstream:
                    downstream = web.WebSocketResponse(protocols=[upstream.protocol] if upstream.protocol else (), max_msg_size=64 * 1024**2)
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
                        await downstream.close()
                    return downstream
            async with app[SESSION].request(request.method, target, headers=headers,
                    data=request.content if request.can_read_body else None, allow_redirects=False) as upstream:
                outgoing = [(k, v) for k, v in clean_headers(upstream.headers) if k.lower() not in {"set-cookie", "location"}]
                response = web.StreamResponse(status=upstream.status, headers=outgoing)
                response.headers["Strict-Transport-Security"] = "max-age=31536000"
                response.headers["X-Content-Type-Options"] = "nosniff"
                if "Location" in upstream.headers:
                    response.headers["Location"] = redirect(upstream.headers["Location"], config, {host: port})
                for value in secure_cookies(upstream.headers):
                    response.headers.add("Set-Cookie", value)
                await response.prepare(request)
                async for chunk in upstream.content.iter_chunked(65536):
                    if time.time() >= expiry:
                        break
                    await response.write(chunk)
                await response.write_eof()
                return response
        except Denied as error:
            message = "Service unavailable. Start the course service and retry.\n" if error.status == 503 else "Workshop access denied.\n"
            return web.Response(status=error.status, text=message, headers={"Cache-Control": "no-store"})
        except (OSError, asyncio.TimeoutError, ClientError):
            return web.Response(status=503, text="Service unavailable. Start the course service and retry.\n", headers={"Cache-Control": "no-store"})

    app.router.add_route("*", "/{tail:.*}", handle)
    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    app = application(json.loads(args.config.read_text()))
    web.run_app(app, host=args.bind, port=args.port, access_log=None, print=None)


if __name__ == "__main__":
    main()
