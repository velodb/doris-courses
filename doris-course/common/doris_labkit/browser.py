"""Translate browser links without changing host-side HTTP or container routes."""

import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit


def safe_path(path):
    decoded = path
    for _ in range(4):
        decoded = unquote(decoded)
    return "\\" not in decoded and not any(p in {".", ".."} for p in decoded.split("/"))


def validate_routes(routes, event_id, learner_id):
    if (not isinstance(routes, dict) or routes.get("schema_version") != 1
            or not event_id or not learner_id
            or routes.get("event_id") != event_id or routes.get("learner_id") != learner_id
            or not isinstance(routes.get("ports"), dict)):
        raise ValueError("Browser routes do not belong to this event and learner.")
    services = routes.get("services", {})
    if not isinstance(services, dict) or any(not isinstance(s, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", s) for s in services):
        raise ValueError("Browser routes contain an invalid service name.")
    for port in routes["ports"]:
        if not isinstance(port, str) or not re.fullmatch(r"[1-9][0-9]{0,4}", port) or int(port) > 65535:
            raise ValueError("Browser routes contain an invalid port.")
    for destination in [*routes["ports"].values(), *services.values()]:
        if not isinstance(destination, str):
            raise ValueError("Browser route must be a URL.")
        target = urlsplit(destination)
        if (target.scheme not in {"http", "https"} or not target.hostname or target.username or target.password
                or target.query or target.fragment or any(c.isspace() for c in destination)
                or "\\" in destination or not safe_path(target.path)
                or (target.scheme == "http" and target.hostname not in {"localhost", "127.0.0.1", "::1"})):
            raise ValueError("Browser routes require HTTPS or loopback HTTP, without credentials or query.")
        _ = target.port  # Validate an explicit destination port too.
    return routes


def browser_url(url, *, service=None, routes=None, event_id=None, learner_id=None):
    """Map a loopback web URL by its exact port; leave other URLs unchanged.

    An explicit remote configuration rejects unmapped loopback ports instead of
    silently sending the browser to its own machine. Internal clients must keep
    their original URLs. Relative paths and external references are unchanged.
    """
    if routes is None:
        filename = os.environ.get("DORIS_BROWSER_ROUTES_FILE")
        if not filename:
            return url
        routes = json.loads(Path(filename).read_text())
        event_id = os.environ.get("DORIS_WORKSHOP_EVENT_ID")
        learner_id = os.environ.get("DORIS_WORKSHOP_LEARNER_ID")
    validate_routes(routes, event_id, learner_id)
    source = urlsplit(url)
    if source.scheme not in {"http", "https", "ws", "wss"} or source.hostname not in {"localhost", "127.0.0.1", "::1"}:
        return url
    if source.username or source.password:
        raise ValueError("Browser links must not contain credentials.")
    if not safe_path(source.path):
        raise ValueError("Browser links must stay inside the configured service path.")
    port = source.port or (443 if source.scheme in {"https", "wss"} else 80)
    if service and "services" in routes:
        if service not in routes["services"]:
            raise ValueError("No protected browser route for this service.")
        destination = routes["services"][service]
    elif str(port) in routes["ports"]:
        destination = routes["ports"][str(port)]
    else:
        raise ValueError("No browser route for this port; configure its protected endpoint or SSM forward.")
    target = urlsplit(destination)
    scheme = target.scheme
    if source.scheme in {"ws", "wss"}:
        scheme = "wss" if scheme == "https" else "ws"
    return urlunsplit((scheme, target.netloc, target.path.rstrip("/") + source.path,
                       source.query, source.fragment))


def browser_redirect(location, request_url, **kwargs):
    """Resolve a service Location header before applying the same URL mapping."""
    return browser_url(urljoin(request_url, location), **kwargs)


def register_browser_port(service, port):
    """Publish a discovered port to this host's gateway, without changing internal URLs."""
    filename = os.environ.get("DORIS_WORKSHOP_PORT_REGISTRY")
    if not filename:
        return
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", service) or type(port) is not int or not 1024 <= port <= 65535 or port == 8080:
        raise ValueError("Invalid dynamic browser service port.")
    routes = json.loads(Path(os.environ["DORIS_BROWSER_ROUTES_FILE"]).read_text())
    event, learner = os.environ.get("DORIS_WORKSHOP_EVENT_ID"), os.environ.get("DORIS_WORKSHOP_LEARNER_ID")
    validate_routes(routes, event, learner)
    if service not in routes.get("services", {}):
        raise ValueError("Dynamic service is not assigned to this learner.")
    path = Path(filename)
    import fcntl
    with path.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        record = {"schema_version": 1, "event_id": event, "learner_id": learner, "ports": {}}
        if path.exists():
            record = json.loads(path.read_text())
            if record.get("event_id") != event or record.get("learner_id") != learner or record.get("schema_version") != 1:
                raise ValueError("Dynamic registry belongs to another learner.")
        record["ports"][service] = port
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(record, stream, allow_nan=False)
            os.replace(temporary, path)
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
