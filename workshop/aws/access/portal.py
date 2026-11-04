"""Operator-owned course entry; only signed activity assignments select links."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlencode

from aiohttp import ClientSession, ClientTimeout, DummyCookieJar, web

# The installed directory is root-owned; isolated Python omits the script path.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from gateway import Denied, HOST, ID, Verifier, validate_config as validate_gateway

UI_ROOT = Path(__file__).resolve().parent / "ui"
if not UI_ROOT.is_dir():
    UI_ROOT = Path(__file__).resolve().parents[3] / "ui/workshop"
sys.path.insert(0, str(UI_ROOT))
from doris_workshop_ui import Renderer, asset as ui_asset

PATH = "/doris-courses/"
VERIFIERS = web.AppKey("portal_verifiers", dict)


def validate_config(config):
    if set(config) != {"schema_version", "domain", "alb_arn", "region", "activities"} or config["schema_version"] != 1:
        raise ValueError("Invalid portal fields")
    if not isinstance(config["domain"], str) or not HOST.fullmatch(config["domain"]) or len(config["domain"]) > 190:
        raise ValueError("Invalid portal namespace")
    if not isinstance(config["region"], str) or not re.fullmatch(r"[a-z]{2}-[a-z-]+-\d", config["region"]):
        raise ValueError("Invalid portal Region")
    if not isinstance(config["alb_arn"], str) or not re.fullmatch(
            r"arn:aws:elasticloadbalancing:" + re.escape(config["region"]) +
            r":\d{12}:loadbalancer/app/[a-zA-Z0-9-]+/[a-f0-9]{16}", config["alb_arn"]):
        raise ValueError("Invalid portal ALB")
    if not isinstance(config["activities"], dict):
        raise ValueError("Register activities as an explicit map")
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


def page(html, *, status=200):
    return web.Response(text=html, status=status, content_type="text/html")


def application(config, *, verifiers=None):
    validate_config(config)
    ui = Renderer(asset_base="https://" + config["domain"] + PATH + "static",
        home="https://" + config["domain"] + PATH)

    @web.middleware
    async def security(request, handler):
        try:
            response = await handler(request)
        except Denied as denied:
            entry = next((f'https://{event}-entry.{config["domain"]}{PATH}' for event in config["activities"]
                if request.headers.get("Host") == f'{event}-entry.{config["domain"]}'), None)
            response = page(ui.state("expired" if denied.status == 401 else "denied",
                destination=entry if denied.status == 401 else None), status=denied.status)
        # Chrome applies form-action to the logout redirect as well as the POST.
        # Allow only this entry's configured Cognito logout endpoint.
        activity = next((value for event, value in config["activities"].items()
            if request.headers.get("Host") == f'{event}-entry.{config["domain"]}'), None)
        form_action = "'self'" + (" " + activity["login_origin"] + "/logout" if activity else "")
        response.headers.update({"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; style-src 'self' https://" + config["domain"] +
                "; img-src 'self' https://" + config["domain"] + "; font-src 'self' https://" + config["domain"] +
                "; base-uri 'none'; frame-ancestors 'none'; form-action " + form_action,
            # no-referrer makes a same-origin form POST send Origin: null in
            # browsers, which cannot pass the strict logout Origin check.
            # same-origin still withholds the referrer from Cognito and other
            # cross-origin destinations.
            "Referrer-Policy": "same-origin"})
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
            if request.path.startswith(PATH + "static/") and request.method in {"GET", "HEAD"}:
                try:
                    data, mime = ui_asset(request.path[len(PATH + "static/"):])
                except KeyError:
                    raise Denied(404)
                return web.Response(body=data, content_type=mime, headers={"Access-Control-Allow-Origin": "*"})
            if request.path == PATH + "help" and request.method in {"GET", "HEAD"}:
                return page(ui.state("help"))
            if request.path not in {PATH, PATH.rstrip("/")} or request.method not in {"GET", "HEAD"}:
                raise Denied(403)
            links = [{"id": event, "href": f'https://{event}-entry.{config["domain"]}{PATH}'}
                for event, activity in sorted(config["activities"].items()) if active(activity)]
            return page(ui.activities(links))
        event = next((event for event in config["activities"] if host == f'{event}-entry.{config["domain"]}'), None)
        if event is None or not active(config["activities"][event]):
            raise Denied(403)
        activity = config["activities"][event]
        if request.path == "/_workshop/logged-out" and request.method in {"GET", "HEAD"}:
            return page(ui.state("signed-out"))
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
        services = assignment["services"]
        jupyter = services["jupyter"]
        tools = [{"id": "jupyter", "title": "Notebooks", "href": jupyter + "/lab", "external": True},
            {"id": "terminal", "title": "Web Terminal", "href": jupyter + "/workshop/terminal", "external": True}]
        primary = next((name for name in services if ui.service_role(name) == "doris"), None)
        if primary:
            tools.append({"id": primary, "title": ui.service_label(primary), "href": services[primary] + "/", "external": True})
        extra = [{"id": name, "title": ui.service_label(name), "href": url + "/"}
            for name, url in sorted(services.items()) if name not in {"jupyter", primary}]
        return page(ui.workspace(event=event, learner=assignment["learner_id"], course=jupyter + "/workshop/",
            tools=tools, extra=extra, logout="/_workshop/logout"))

    app.router.add_route("*", "/{tail:.*}", handle)
    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    web.run_app(application(json.loads(args.config.read_text())), host="0.0.0.0", port=8080, access_log=None, print=None)
