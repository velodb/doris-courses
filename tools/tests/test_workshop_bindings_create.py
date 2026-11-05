"""Initial bindings creation review and failure boundaries."""
from contextlib import nullcontext
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import workshop_bindings_create as create
import workshop_config as wc


def fixture(*, portal=False):
    access = {"hosted_zone_id": "zone", "alb_arn": "alb", "alb_dns_name": "alb.example", "alb_zone_id": "azone",
              "https_listener_arn": "listener", "vpc_id": "vpc"}
    v = {"event_id": "event", "expected_account_id": "123456789012", "access": access,
         "learners": {"alice": "i-alice"}, "routes": {"alice:jupyter": {"learner_id": "alice", "hostname": "alice.example", "priority": 100}}, "portal": None}
    rows = []
    def add(typ, name, after, key=None):
        addr = typ + "." + name + ("[" + json.dumps(key) + "]" if key is not None else "")
        row = {"mode": "managed", "address": addr, "type": typ, "change": {"actions": ["create"], "after": after}}
        if key is not None: row["index"] = key
        rows.append(row)
    add("aws_cognito_user_pool", "activity", {"name": "doris-workshop-event", "admin_create_user_config": [{"allow_admin_create_user_only": True}]})
    add("aws_cognito_user_pool_client", "alb", {"generate_secret": True, "allowed_oauth_flows": ["code"],
        "callback_urls": ["https://alice.example/oauth2/idpresponse"], "logout_urls": ["https://alice.example/_workshop/logged-out"]})
    add("aws_cognito_user_pool_domain", "activity", {"domain": "dws-event-123456789012"})
    add("aws_lb_target_group", "learner", {"vpc_id": "vpc", "port": 8080, "protocol": "HTTP", "target_type": "instance"}, "alice")
    add("aws_lb_target_group_attachment", "learner", {"target_id": "i-alice", "port": 8080}, "alice")
    add("aws_route53_record", "learner", {"zone_id": "zone", "type": "A", "name": "alice.example", "alias": [{"name": "alb.example", "zone_id": "azone"}]}, "alice:jupyter")
    for offset, kind in enumerate(("logout", "api", "page")):
        action = [{"type": "fixed-response"}] if kind == "logout" else [{"type": "authenticate-cognito", "authenticate_cognito": [
            {"on_unauthenticated_request": "deny" if kind == "api" else "authenticate"}]}, {"type": "forward"}]
        conditions = [{"host_header": [{"values": ["alice.example"]}]}]
        if kind != "page":
            conditions.append({"path_pattern": [{"values": ["/_workshop/logged-out"] if kind == "logout" else ["/api/*", "/terminals/*"]}]})
        add("aws_lb_listener_rule", "learner", {"listener_arn": "listener", "priority": 100 + offset,
            "condition": conditions, "action": action}, "alice:jupyter-" + kind)
    if portal:
        access["portal"] = {"target_group_arn": "owned-portal-target"}
        v["portal"] = {"hostname": "event-entry.example", "priority": 200}
        client = rows[1]["change"]["after"]
        client["callback_urls"].append("https://event-entry.example/oauth2/idpresponse")
        client["logout_urls"].append("https://event-entry.example/_workshop/logged-out")
        add("aws_route53_record", "learner", {"zone_id": "zone", "type": "A", "name": "event-entry.example",
            "alias": [{"name": "alb.example", "zone_id": "azone"}]}, "portal")
        for offset, kind in enumerate(("logout", "api", "page")):
            conditions = [{"host_header": [{"values": ["event-entry.example"]}]}]
            if kind != "page":
                conditions.append({"path_pattern": [{"values": ["/_workshop/logged-out"] if kind == "logout" else ["/api/*", "/terminals/*"]}]})
            action = [{"type": "forward", "target_group_arn": "owned-portal-target", "forward": []}]
            if kind != "logout":
                action.insert(0, {"type": "authenticate-cognito", "authenticate_cognito": [
                    {"on_unauthenticated_request": "deny" if kind == "api" else "authenticate"}]})
            add("aws_lb_listener_rule", "learner", {"listener_arn": "listener", "priority": 200 + offset,
                "condition": conditions, "action": action}, "portal-" + kind)
    return v, {"complete": True, "variables": {k: {"value": val} for k, val in v.items()}, "resource_changes": rows}


class BindingCreateTests(unittest.TestCase):
    def test_portal_signed_out_page_forwards_to_owned_portal(self):
        variables, plan = fixture(portal=True)
        self.assertEqual(len(create.validate_plan(variables, plan)), 13)

    def test_logout_forward_cannot_expose_other_paths_hosts_or_targets(self):
        variables, plan = fixture(portal=True)
        portal = next(row for row in plan["resource_changes"] if row.get("index") == "portal-logout")
        learner = next(row for row in plan["resource_changes"] if row.get("index") == "alice:jupyter-logout")
        edits = [
            (portal, lambda after: after["condition"][1]["path_pattern"][0].update(values=["/doris-courses/"])),
            (portal, lambda after: after["condition"][1]["path_pattern"][0].update(values=["/_workshop/logged-out", "/*"])),
            (portal, lambda after: after["condition"][1]["path_pattern"][0].update(regex_values=[".*"])),
            (portal, lambda after: after["action"][0].update(target_group_arn="foreign-target")),
            (portal, lambda after: after["action"][0].update(forward=[{"target_group": [{"arn": "foreign-target"}]}])),
            (learner, lambda after: after.update(action=copy.deepcopy(portal["change"]["after"]["action"]))),
        ]
        for row, edit in edits:
            with self.subTest(address=row["address"], edit=edit):
                broken = copy.deepcopy(plan)
                after = next(item["change"]["after"] for item in broken["resource_changes"] if item["address"] == row["address"])
                edit(after)
                with self.assertRaises(ValueError):
                    create.validate_plan(variables, broken)

    def test_create_plan_checks_exact_addresses_and_security(self):
        v, plan = fixture()
        self.assertEqual(len(create.validate_plan(v, plan)), 9)
        edits = [lambda p: p["resource_changes"].pop(),
                 lambda p: p["resource_changes"].append(copy.deepcopy(p["resource_changes"][0])),
                 lambda p: p["resource_changes"][0]["change"].update(actions=["delete"]),
                 lambda p: p["resource_changes"][4]["change"]["after"].update(target_id="i-other"),
                 lambda p: p["resource_changes"][5]["change"]["after"].update(zone_id="foreign"),
                 lambda p: p["resource_changes"][7]["change"]["after"]["action"][0]["authenticate_cognito"][0].update(on_unauthenticated_request="allow"),
                 lambda p: p.update(complete=False)]
        for edit in edits:
            broken = copy.deepcopy(plan)
            edit(broken)
            with self.assertRaises(ValueError): create.validate_plan(v, broken)

    def test_conflicting_listener_priority_is_rejected(self):
        v, _ = fixture()
        v["access"].update(access_id="access", region="us-east-1")
        controller = mock.Mock()
        controller.config = {"owner_id": "owner", "domain": "example"}
        cloud = controller.cloud
        cloud.call.side_effect = lambda service, operation, **kwargs: {
            ("elbv2", "describe_load_balancers"): {"LoadBalancers": [{"VpcId": "vpc", "State": {"Code": "active"}, "DNSName": "alb.example", "CanonicalHostedZoneId": "azone"}]},
            ("elbv2", "describe_tags"): {"TagDescriptions": [{"ResourceArn": "alb", "Tags": [{"Key": k, "Value": val} for k, val in {"Project": "doris-workshop", "Scope": "shared-access", "Owner": "owner", "Access": "access"}.items()]}]},
        }[(service, operation)]
        cloud.items.side_effect = lambda service, operation, key, **kwargs: {
            "describe_listeners": [{"ListenerArn": "listener", "Protocol": "HTTPS", "Port": 443}],
            "describe_rules": [{"Priority": "default"}, {"Priority": "101"}],
        }[operation]
        with self.assertRaisesRegex(ValueError, "priorities are already in use"):
            create.headroom(controller, v)

    def test_headroom_covers_all_listeners_and_exact_dns_names(self):
        v, _ = fixture()
        v["access"].update(access_id="access", region="us-east-1")
        controller = mock.Mock()
        controller.config = {"owner_id": "owner", "domain": "example.com"}
        cloud = controller.cloud
        cloud.call.side_effect = lambda service, operation, **kwargs: {
            ("elbv2", "describe_load_balancers"): {"LoadBalancers": [{"VpcId": "vpc", "State": {"Code": "active"}, "DNSName": "alb.example", "CanonicalHostedZoneId": "azone"}]},
            ("elbv2", "describe_tags"): {"TagDescriptions": [{"ResourceArn": "alb", "Tags": [{"Key": k, "Value": val} for k, val in {"Project": "doris-workshop", "Scope": "shared-access", "Owner": "owner", "Access": "access"}.items()]}]},
            ("route53", "get_hosted_zone"): {"HostedZone": {"Name": "example.com.", "Config": {"PrivateZone": False}}},
        }[(service, operation)]
        records = [{"Name": "example.com."}]
        def items(service, operation, key, **kwargs):
            if operation == "describe_listeners": return [{"ListenerArn": "listener", "Protocol": "HTTPS", "Port": 443}, {"ListenerArn": "http"}]
            if operation == "describe_rules": return [{"Priority": "default"}] + ([{"Priority": "5"}] if kwargs["ListenerArn"] == "http" else [])
            if operation == "describe_account_limits": return [{"Name": "rules-per-application-load-balancer", "Max": "4"}, {"Name": "target-groups", "Max": "10"}]
            if operation == "describe_target_groups": return []
            if operation == "list_resource_record_sets": return records
            raise AssertionError(operation)
        cloud.items.side_effect = items
        create.headroom(controller, v)
        records.append({"Name": "alice.example."})
        with self.assertRaisesRegex(ValueError, "DNS name already exists"):
            create.headroom(controller, v)

    def test_cli_holds_scope_change_fence(self):
        c = mock.Mock()
        c.config, c.pilot, c.cloud = {}, {}, object()
        with mock.patch.object(create.lifecycle, "run_lock", return_value=nullcontext()), \
                mock.patch.object(create.lifecycle, "Controller", return_value=c), \
                mock.patch.object(create.ttl, "activity_operation", return_value=nullcontext()) as fence, \
                mock.patch.object(create, "prepare", return_value={"review_sha256": "fixture"}), \
                mock.patch("sys.stdout", new_callable=io.StringIO):
            args = ["plan"]
            for name in ("pilot-run", "bindings-run", "route", "output"):
                args.extend(["--" + name, "/tmp/fixture"])
            self.assertEqual(create.main(args), 0)
        self.assertTrue(fence.call_args.kwargs["scope_change"])

    def test_review_apply_and_partial_failure_never_replay(self):
        variables, plan = fixture()
        class FakeTerraform:
            attempts = 0
            fail = True
            def __init__(self, module, directory): self.path = Path(directory)
            def init(self, backend):
                (self.path / "terraform-data").mkdir()
                (self.path / "terraform-data/terraform.tfstate").write_text("backend")
            def state(self): return {"resources": []}
            def call(self, args, *, capture=False):
                if args[0] == "plan":
                    Path(next(x[5:] for x in args if x.startswith("-out="))).write_bytes(b"saved-plan")
                    return ""
                if args[0] == "output": return json.dumps({"event_id": "event"})
                return json.dumps(plan)
            def apply(self, path):
                type(self).attempts += 1
                if self.fail: raise RuntimeError("partial result")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run = root / "bindings"
            run.mkdir()
            wc.write_manifest(run / "backend.tfbackend.json", {})
            wc.write_manifest(run / "inputs.tfvars.json", variables)
            route = root / "route.json"
            wc.write_manifest(route, {})
            controller = mock.Mock(config={"event_id": "event"})
            with mock.patch.object(create, "context", return_value=variables), mock.patch.object(create, "headroom") as headroom, \
                    mock.patch.object(create, "refresh_preflight", return_value={"status": "passed", "valid_until": "2099-01-01T00:00:00Z"}), \
                    mock.patch.object(create, "hashes", return_value={"bound": "fixture"}), \
                    mock.patch.object(create.update, "verify_state"):
                result = create.prepare(controller, run, route, root / "review", tf_factory=FakeTerraform)
                with self.assertRaises(RuntimeError):
                    create.apply(controller, run, route, root / "review", result["review_sha256"], tf_factory=FakeTerraform)
                with self.assertRaisesRegex(ValueError, "previous apply"):
                    create.apply(controller, run, route, root / "review", result["review_sha256"], tf_factory=FakeTerraform)
                self.assertEqual(FakeTerraform.attempts, 1)
                self.assertEqual(json.loads((root / "review/progress.json").read_text())["phase"], "applying")
                self.assertEqual(headroom.call_count, 3)
                FakeTerraform.fail = False
                good = create.prepare(controller, run, route, root / "review-good", tf_factory=FakeTerraform)
                completed = create.apply(controller, run, route, root / "review-good", good["review_sha256"], tf_factory=FakeTerraform)
                self.assertEqual(completed["phase"], "complete")
                self.assertEqual(json.loads(Path(completed["identity_file"]).read_text())["event_id"], "event")
                self.assertFalse(completed["authenticated_access_verified"])


if __name__ == "__main__":
    unittest.main()
