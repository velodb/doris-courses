"""Private, listener-scoped routing reservations with stable tombstones."""
from contextlib import contextmanager
import copy
import fcntl
import os
from pathlib import Path
import re
import tempfile

import workshop_config as wc

ID = re.compile(r"[a-z][a-z0-9-]{0,31}")


def require(condition, message):
    if not condition:
        raise wc.ConfigError("routing: " + message)


@contextmanager
def lock(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(str(path) + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(descriptor, "a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise wc.ConfigError("routing: another generator holds this allocation file") from exc
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def reserve(config, access, hosts, *, previous=None, priority_base=1000, priority_limit=1999):
    """Reserve one exclusive interval per activity; never reuse withdrawn slots."""
    require(type(priority_base) is int and type(priority_limit) is int and 1 <= priority_base <= priority_limit <= 50000,
        "reserve an explicit integer priority interval within 1..50000")
    scope = {"account_id": access["account_id"], "region": access["region"], "domain": access["domain"], "alb_arn": access["alb_arn"]}
    ledger = {"schema_version": 1, "kind": "aws-workshop-routing-allocations", "scope": scope, "events": {}}
    if previous is not None:
        require(set(previous) == set(ledger) and previous["schema_version"] == 1 and previous["kind"] == ledger["kind"] and
            previous["scope"] == scope and isinstance(previous["events"], dict), "allocation file belongs to another listener/namespace")
        ledger = copy.deepcopy(previous)
    intervals, host_owners = [], {}
    for event, reservation in ledger["events"].items():
        require(ID.fullmatch(event) and set(reservation) == {"owner_id", "priority_base", "priority_limit", "hosts"} and
            isinstance(reservation["owner_id"], str) and ID.fullmatch(reservation["owner_id"]), "invalid activity reservation")
        base, limit = reservation["priority_base"], reservation["priority_limit"]
        require(type(base) is int and type(limit) is int and 1 <= base <= limit <= 50000 and
            all(limit < start or base > end for start, end in intervals), "overlapping or invalid activity ranges")
        intervals.append((base, limit))
        require(isinstance(reservation["hosts"], dict), "invalid host allocations")
        priorities = set()
        for key, value in reservation["hosts"].items():
            require(set(value) == {"hostname", "priority", "active"} and type(value["active"]) is bool and
                type(value["priority"]) is int and base <= value["priority"] <= limit - 2 and
                (value["priority"] - base) % 3 == 0 and value["priority"] not in priorities, "invalid or overlapping host allocation")
            require(isinstance(key, str) and (key == "portal" or len(key.split(":")) == 2 and
                all(ID.fullmatch(part) for part in key.split(":"))), "invalid host ownership key")
            expected = f'{event}-entry.{scope["domain"]}' if key == "portal" else f'{event}-{key.replace(":", "-")}.{scope["domain"]}'
            require(value["hostname"] == expected, "host allocation does not match its activity/learner/service")
            require(value["hostname"] not in host_owners, "hostname is already reserved")
            host_owners[value["hostname"]] = (event, key)
            priorities.add(value["priority"])
    event = config["event_id"]
    if event not in ledger["events"]:
        require(all(priority_limit < start or priority_base > end for start, end in intervals), "range is reserved by another activity")
        ledger["events"][event] = {"owner_id": config["owner_id"], "priority_base": priority_base,
            "priority_limit": priority_limit, "hosts": {}}
    reservation = ledger["events"][event]
    require(reservation["owner_id"] == config["owner_id"] and reservation["priority_base"] == priority_base and
        reservation["priority_limit"] == priority_limit, "owner or reserved interval changed; preserve existing allocations")
    # A root entry is owned outside activity state, even while no event uses it.
    if access.get("portal") is not None:
        root_priority = access["portal"]["listener_priority"]
        require(type(root_priority) is int and all(not (start <= root_priority <= end) for start, end in
            [(row["priority_base"], row["priority_limit"]) for row in ledger["events"].values()]), "shared root priority overlaps an activity interval")
    allocations = reservation["hosts"]
    for value in allocations.values():
        value["active"] = False
    cursor = max((row["priority"] + 3 for row in allocations.values()), default=priority_base)
    for key, hostname in hosts.items():
        require(hostname not in host_owners or host_owners[hostname] == (event, key), "hostname is reserved by another assignment")
        host_owners[hostname] = (event, key)
        if key not in allocations:
            require(cursor + 2 <= priority_limit, "reserved interval exhausted; withdrawn slots cannot be recycled")
            allocations[key] = {"hostname": hostname, "priority": cursor, "active": True}
            cursor += 3
        require(allocations[key]["hostname"] == hostname, "existing hostname changed")
        allocations[key]["active"] = True
    return ledger, {key: allocations[key]["priority"] for key in hosts}


def save(path, ledger):
    path = Path(path)
    with tempfile.TemporaryDirectory(prefix=".routing-", dir=path.parent) as directory:
        temporary = Path(directory) / "allocations.json"
        wc.write_manifest(temporary, ledger)
        os.replace(temporary, path)
