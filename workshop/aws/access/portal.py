"""Operator-owned course entry; only signed activity assignments select links."""
import argparse
from datetime import datetime, timezone
import hashlib
from html import escape
import json
from pathlib import Path
import sys
import time
from urllib.parse import urlencode

from aiohttp import ClientSession, ClientTimeout, DummyCookieJar, web

# The installed directory is root-owned; isolated Python omits the script path.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from gateway import Denied, HOST, ID, Verifier, validate_config as validate_gateway

PATH = "/doris-courses/"
VERIFIERS = web.AppKey("portal_verifiers", dict)


def validate_config(config):
    if set(config) != {"schema_version", "domain", "alb_arn", "region", "activities"} or config["schema_version"] != 1:
        raise ValueError("Invalid portal fields")
    if not isinstance(config["domain"], str) or not HOST.fullmatch(config["domain"]) or len(config["domain"]) > 190:
        raise ValueError("Invalid portal namespace")
    if not isinstance(config["activities"], dict) or not config["activities"]:
        raise ValueError("Register an explicit activity")
    for event, activity in config["activities"].items():
        if not ID.fullmatch(event) or set(activity) != {"expires_at", "issuer", "client_id", "login_origin", "assignments"}:
            raise ValueError("Invalid portal activity")
        assignments = activity["assignments"]
        if not isinstance(assignments, dict) or not assignments:
            raise ValueError("Assign immutable subjects")
        validate_gateway({"schema_version": 1, "event_id": event, "learner_id": "entry",
            "region": config["region"], "alb_arn": config["alb_arn"],
            **{key: activity[key] for key in ("issuer", "client_id", "login_origin", "expires_at")},
            "subjects": list(assignments), "services": {f'{event}-entry.{config["domain"]}': {
                "port": 8888, "jupyter": True, "redirect_origins": []}}})
        for assignment in assignments.values():
            if set(assignment) != {"learner_id", "services"} or not ID.fullmatch(assignment["learner_id"]):
                raise ValueError("Invalid portal assignment")
            if not isinstance(assignment["services"], dict) or "jupyter" not in assignment["services"]:
                raise ValueError("Assign the learner's course entry")
            for service, url in assignment["services"].items():
                hostname = f'{event}-{assignment["learner_id"]}-{service}.{config["domain"]}'
                if not ID.fullmatch(service) or len(hostname.split(".")[0]) > 63 or url != "https://" + hostname:
                    raise ValueError("Portal links must be exact assigned service origins")


def active(activity):
    return time.time() < datetime.strptime(activity["expires_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()


def page(body):
    return web.Response(text='<!doctype html><html lang="en"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>Doris courses</title><body><main><h1>Doris courses</h1>' + body + '</main></body></html>',
        content_type="text/html")


def application(config, *, verifiers=None):
    validate_config(config)

    @web.middleware
    async def security(request, handler):
        try:
            response = await handler(request)
        except Denied as denied:
            response = web.Response(status=denied.status, text="Course entry unavailable or unauthorized.\n")
        response.headers.update({"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
            "Referrer-Policy": "no-referrer"})
        return response

    app = web.Application(middlewares=[security], client_max_size=1024)

    async def context(app):
        async with ClientSession(cookie_jar=DummyCookieJar(), trust_env=False, timeout=ClientTimeout(total=5)) as session:
            app[VERIFIERS] = verifiers if verifiers is not None else {event: Verifier({**activity,
                "alb_arn": config["alb_arn"], "region": config["region"]}, session)
                for event, activity in config["activities"].items()}
            yield
    app.cleanup_ctx.append(context)

    async def handle(request):
        if request.path == "/healthz" and request.method in {"GET", "HEAD"}:
            return web.Response(text="portal\n")
        if len(request.headers.getall("Host", [])) != 1 or request.query_string:
            raise Denied(400)
        host = request.headers["Host"]
        if host == config["domain"]:
            if request.path not in {PATH, PATH.rstrip("/")} or request.method not in {"GET", "HEAD"}:
                raise Denied(403)
            links = [f'<li><a href="https://{event}-entry.{config["domain"]}{PATH}">{escape(event)}</a></li>'
                for event, activity in sorted(config["activities"].items()) if active(activity)]
            return page("<p>Choose your activity to sign in.</p><ul>" + "".join(links) + "</ul>")
        event = next((event for event in config["activities"] if host == f'{event}-entry.{config["domain"]}'), None)
        if event is None or not active(config["activities"][event]):
            raise Denied(403)
        activity = config["activities"][event]
        if request.path == "/_workshop/logged-out" and request.method in {"GET", "HEAD"}:
            return page("<p>Signed out. Close other course tabs.</p>")
        if request.path not in {PATH, PATH.rstrip("/"), "/_workshop/logout"}:
            raise Denied(403)
        if len(request.headers.getall("X-Amzn-Oidc-Data", [])) != 1:
            raise Denied()
        claims, _ = await app[VERIFIERS][event].verify_claims(request.headers["X-Amzn-Oidc-Data"])
        subject = claims.get("sub")
        assignment = activity["assignments"].get(subject) if isinstance(subject, str) else None
        if assignment is None:
            raise Denied(403)
        if request.path == "/_workshop/logout":
            if request.method != "POST" or request.headers.get("Origin") != "https://" + host:
                raise Denied(403)
            response = web.Response(status=303, headers={"Location": activity["login_origin"] + "/logout?" + urlencode({
                "client_id": activity["client_id"], "logout_uri": "https://" + host + "/_workshop/logged-out"})})
            cookie = "DorisAuth" + hashlib.sha256(host.encode()).hexdigest()[:20]
            for suffix in ("", "-0", "-1", "-2", "-3"):
                response.del_cookie(cookie + suffix, path="/", secure=True, httponly=True)
            response.del_cookie("AWSALBAuthNonce", path="/", secure=True, httponly=True)
            return response
        if request.method not in {"GET", "HEAD"} or request.headers.get("Upgrade"):
            raise Denied(405)
        links = [f'<li><a href="{escape(url, quote=True)}{("/workshop/" if service == "jupyter" else "/")}">{escape(service)}</a></li>'
            for service, url in sorted(assignment["services"].items())]
        return page("<p>Your course services</p><ul>" + "".join(links) + '</ul><form method="post" action="/_workshop/logout">'
            '<button type="submit">Sign out of this entry</button></form>')

    app.router.add_route("*", "/{tail:.*}", handle)
    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    web.run_app(application(json.loads(args.config.read_text())), host="0.0.0.0", port=8080, access_log=None, print=None)
