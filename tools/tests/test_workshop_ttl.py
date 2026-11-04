import copy
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import workshop_config as wc
import workshop_ttl as ttl
import workshop_ttl_control as control
import workshop_ttl_runner as runner
import test_workshop_cleanup as fixture

NOW = datetime(2030, 1, 1, 12, tzinfo=timezone.utc)


def binding(revision=1):
    c = fixture.config()
    c["expires_at"] = "2030-01-01T12:00:31Z"
    manifest = fixture.manifest()
    t = manifest["target"]
    module = {"module": "pilot", "backend": {"bucket": "foundation-state-example",
        "key": f'aws-workshop/aws/{t["aws_account_id"]}/{t["region"]}/{t["event_id"]}/terraform.tfstate',
        "region": t["region"], "encrypt": True, "use_lockfile": True, "allowed_account_ids": [t["aws_account_id"]]},
        "vars_sha256": "a" * 64, "module_sha256": "b" * 64}
    return ttl.make_binding(c, manifest, [module], bucket="foundation-state-example", revision=revision,
        source_sha="c" * 40, tree_sha256="d" * 64)


class MemoryStore:
    def __init__(self):
        self.rows, self.serial = {}, 0

    def read(self, key):
        value, etag = self.rows.get(key, (None, None))
        return copy.deepcopy(value), etag

    def compare_set(self, key, expected, value):
        if self.rows.get(key, (None, None))[1] != expected:
            raise ttl.Conflict("ttl: concurrent update")
        self.serial += 1
        self.rows[key] = copy.deepcopy(value), str(self.serial)
        return str(self.serial)


class Adapter:
    def __init__(self, failure=None):
        self.calls, self.failure = [], failure

    def verify(self, bound):
        self.calls.append("verify")
        if self.failure == "verify":
            raise ValueError("PRIVATE-ERROR")

    def plan(self, module, **kwargs):
        self.calls.append("plan:" + module["module"])
        if self.failure == "plan":
            raise TimeoutError("PRIVATE-LOCK")
        return {"resource_changes": []}

    def validate(self, module, plan):
        self.calls.append("validate")

    def apply(self, module, plan, **kwargs):
        self.calls.append("apply:" + module["module"])
        if self.failure == "apply":
            raise TimeoutError("PRIVATE-OUTCOME")

    def reconcile(self):
        self.calls.append("reconcile")
        return {"ok": self.failure != "residual", "account_globally_empty": False}


class BindingTests(unittest.TestCase):
    def test_timezone_normalization_and_minute_ceiling_never_trigger_early(self):
        b = binding()
        result = ttl.schedule(b, now=NOW)
        self.assertEqual(result["schedule_expression"], "at(2030-01-01T12:01:00)")
        self.assertEqual(result["schedule_expression_timezone"], "UTC")
        self.assertEqual(ttl.utc("2030-01-01T20:00:31+08:00"), b["expires_at"])
        self.assertEqual(result["action_after_completion"], "DELETE")
        b["expires_at"] = "2030-01-01T12:00:00Z"
        self.assertEqual(ttl.schedule(b, now=NOW.replace(hour=11))["trigger_at"], b["expires_at"])

    def test_expired_schedule_requires_explicit_due_path(self):
        with self.assertRaises(wc.ConfigError):
            ttl.schedule(binding(), now=NOW.replace(hour=13))

    def test_backend_source_commands_and_revision_are_strict(self):
        for mutate in (lambda b: b.update(command="terraform destroy"), lambda b: b.update(revision=True),
                lambda b: b["modules"][0]["backend"].update(key="foundation/terraform.tfstate"),
                lambda b: b["modules"][0]["backend"].update(use_lockfile=False),
                lambda b: b["modules"][0].update(module="shared-access"), lambda b: b.update(source_sha="latest")):
            b = binding()
            mutate(b)
            with self.assertRaises(wc.ConfigError):
                ttl.validate_binding(b)

    def test_runtime_artifacts_cannot_select_arbitrary_files_or_unbounded_payloads(self):
        for name, size in (("../inputs.tfvars.json", 1), ("runtime.json", 4 * 1024 ** 2 + 1), ("source.tar", True)):
            b = binding()
            b["runtime_artifacts"] = [{"name": name, "size": size, "sha256": "a" * 64}]
            with self.assertRaises(wc.ConfigError):
                ttl.validate_binding(b)

    def test_runtime_bundle_requires_declared_lockfiles_and_matching_hashes(self):
        import hashlib
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lock = root / 'doris-rta.requirements.lock'
            lock.write_bytes(b'locked-dependency')
            digest = hashlib.sha256(lock.read_bytes()).hexdigest()
            (root / 'runtime.json').write_text(json.dumps({'sha256': {lock.name: digest}}))
            artifacts = ttl.runtime_bundle_artifacts(root)
            self.assertEqual({x['name'] for x in artifacts}, {'runtime.json', lock.name})
            b = binding()
            b['runtime_artifacts'] = artifacts
            ttl.validate_binding(b)
            lock.write_bytes(b'changed')
            with self.assertRaisesRegex(wc.ConfigError, 'hash differs'):
                ttl.runtime_bundle_artifacts(root)
            lock.unlink()
            with self.assertRaisesRegex(wc.ConfigError, 'exactly its declared files'):
                ttl.runtime_bundle_artifacts(root)


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.binding, self.store = binding(), MemoryStore()
        ttl.register(self.store, self.binding)
        self.event = ttl.trigger(self.binding)
        self.due = NOW.replace(hour=13)

    def current(self):
        return self.store.read(self.binding["control"]["key"])[0]

    def test_due_cleanup_and_duplicate_delivery(self):
        adapter = Adapter()
        result = ttl.execute(self.binding, self.event, self.store, adapter, now=self.due)
        self.assertEqual(result["outcome"], "complete")
        self.assertEqual(adapter.calls, ["verify", "plan:pilot", "validate", "apply:pilot", "reconcile"])
        calls = list(adapter.calls)
        self.assertFalse(ttl.execute(self.binding, self.event, self.store, adapter, now=self.due)["deleted"])
        self.assertEqual(adapter.calls, calls)
        with self.assertRaises(wc.ConfigError):
            ttl.cancel(self.store, self.binding)

    def test_early_stale_cancelled_and_command_injection_never_apply(self):
        adapter = Adapter()
        self.assertEqual(ttl.execute(self.binding, self.event, self.store, adapter, now=NOW)["outcome"], "not-due")
        stale = {**self.event, "revision": 2}
        self.assertEqual(ttl.execute(self.binding, stale, self.store, adapter, now=self.due)["outcome"], "stale")
        with self.assertRaises(wc.ConfigError):
            ttl.execute(self.binding, {**self.event, "command": "delete-all"}, self.store, adapter, now=self.due)
        ttl.cancel(self.store, self.binding)
        self.assertEqual(ttl.execute(self.binding, self.event, self.store, adapter, now=self.due)["outcome"], "cancelled")
        self.assertEqual(adapter.calls, [])

    def test_renewal_uses_cas_and_old_events_cannot_clean_new_binding(self):
        newer = binding(2)
        newer["expires_at"] = "2030-01-02T12:00:00Z"
        ttl.register(self.store, newer, expected_revision=1)
        self.assertEqual(ttl.execute(self.binding, self.event, self.store, Adapter(), now=self.due)["outcome"], "stale")
        with self.assertRaises(wc.ConfigError):
            ttl.register(self.store, binding(3), expected_revision=1)

    def test_operator_and_ttl_share_fence_without_stealing(self):
        with ttl.operation(self.store, self.binding, holder="operator-up", now=NOW):
            with self.assertRaises(ttl.Conflict):
                ttl.execute(self.binding, self.event, self.store, Adapter(), now=self.due)
            with self.assertRaises(wc.ConfigError):
                ttl.cancel(self.store, self.binding)
            with self.assertRaises(wc.ConfigError):
                ttl.register(self.store, binding(2), expected_revision=1)
        self.assertEqual(self.current()["state"], "active")

    def test_unknown_operator_or_apply_outcome_is_not_automatically_retried(self):
        with self.assertRaises(TimeoutError):
            with ttl.operation(self.store, self.binding, holder="operator", now=NOW):
                raise TimeoutError("PRIVATE")
        self.assertEqual(self.current()["state"], "unknown")
        with self.assertRaises(wc.ConfigError):
            ttl.execute(self.binding, self.event, self.store, Adapter(), now=self.due)
        self.store = MemoryStore()
        ttl.register(self.store, self.binding)
        with self.assertRaises(TimeoutError):
            ttl.execute(self.binding, self.event, self.store, Adapter("apply"), now=self.due)
        self.assertEqual(self.current()["state"], "unknown")

    def test_failed_before_apply_has_finite_attempt_budget(self):
        for _ in range(3):
            with self.assertRaises(TimeoutError):
                ttl.execute(self.binding, self.event, self.store, Adapter("plan"), now=self.due)
        self.assertEqual(self.current()["attempts"], 3)
        with self.assertRaises(wc.ConfigError):
            ttl.execute(self.binding, self.event, self.store, Adapter(), now=self.due)

    def test_partial_cleanup_or_bad_reconciliation_cannot_report_complete(self):
        with self.assertRaises(wc.ConfigError):
            ttl.execute(self.binding, self.event, self.store, Adapter("residual"), now=self.due)
        self.assertEqual(self.current()["state"], "unknown")
        self.assertNotIn("PRIVATE", json.dumps(self.current()))

    def test_lost_claim_reply_leaves_fence_without_running_adapter(self):
        original = self.store.compare_set
        def lost(key, etag, value):
            result = original(key, etag, value)
            if value["state"] == "cleaning":
                raise TimeoutError("PRIVATE")
            return result
        self.store.compare_set = lost
        adapter = Adapter()
        with self.assertRaises(TimeoutError):
            ttl.execute(self.binding, self.event, self.store, adapter, now=self.due)
        self.assertEqual(self.current()["state"], "cleaning")
        self.assertEqual(adapter.calls, ["verify"])

    def test_expired_task_budget_never_reaches_apply(self):
        tick = iter([0, 0, 0, 4000])
        adapter = Adapter()
        with self.assertRaises(wc.ConfigError):
            ttl.execute(self.binding, self.event, self.store, adapter, now=self.due, clock=lambda: next(tick))
        self.assertNotIn("apply:pilot", adapter.calls)
        self.assertEqual(self.current()["state"], "failed")

    def test_every_module_is_validated_before_any_apply(self):
        b = binding()
        access = copy.deepcopy(b["modules"][0])
        access["module"] = "access-bindings"
        access["backend"]["key"] = access["backend"]["key"].replace("terraform.tfstate", "access.tfstate")
        b["modules"].insert(0, access)
        store = MemoryStore()
        ttl.register(store, b)
        adapter = Adapter()
        original = adapter.plan
        def fail_second(module, **kwargs):
            if module["module"] == "pilot":
                raise ValueError("unreviewed-hosts")
            return original(module, **kwargs)
        adapter.plan = fail_second
        with self.assertRaises(ValueError):
            ttl.execute(b, ttl.trigger(b), store, adapter, now=self.due)
        self.assertFalse(any(call.startswith("apply:") for call in adapter.calls))


class TerraformAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "source"
        self.module = self.root / "workshop/aws/terraform/pilot"
        self.module.mkdir(parents=True)
        (self.module / "main.tf").write_text('terraform {}\n')
        self.manifest = fixture.manifest()
        c = fixture.config()
        c["expires_at"] = "2030-01-01T12:00:31Z"
        self.inputs = {"expected_account_id": c["aws_account_id"], "region": c["region"], "event_id": c["event_id"],
            "owner_id": c["owner_id"], "expires_at": c["expires_at"], "learner_ids": ["alice"], "aws_profile": None, "credential_mode": "task-role"}
        item = binding()["modules"][0]
        item.update(module_sha256=wc.canonical_hash({"main.tf": runner.file_hash(self.module / "main.tf")}), vars_sha256=wc.canonical_hash(self.inputs))
        self.bound = ttl.make_binding(c, self.manifest, [item], bucket="foundation-state-example", revision=1,
            source_sha="c" * 40, tree_sha256=runner.tree_hash(self.root))
        self.commands = []
        self.row = fixture.state_row()
        self.plan = {"resource_changes": [{"mode": "managed", "address": self.row["address"],
            "change": {"actions": ["delete"], "before": self.row["values"]}}]}

    def process(self, command, **kwargs):
        self.commands.append(command)
        if "plan" in command:
            Path(next(value.removeprefix("-out=") for value in command if value.startswith("-out="))).write_bytes(b"fresh-plan")
        if "show" in command:
            if command[-1].endswith("refresh.tfplan"):
                value = getattr(self, 'refresh_proof', {})
            else:
                value = self.plan if command[-1].endswith("tfplan") else {"format_version": "1.0", "values": {"root_module": {"resources": [self.row]}}}
            return SimpleNamespace(returncode=0, stdout=json.dumps(value), stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="PRIVATE-TERRAFORM")

    def adapter(self):
        return runner.TerraformAdapter(self.root, Path(self.temp.name) / "work", self.manifest, {"pilot": self.inputs},
            source_sha="c" * 40, identity={"Account": "000000000000", "Arn": "arn:aws:sts::000000000000:assumed-role/cleanup/fixture"},
            expected_role_arn="arn:aws:iam::000000000000:role/cleanup", environment={}, runner=self.process,
            reconciliation=lambda: fixture.cleanup.reconcile(self.manifest, fixture.snapshot(self.manifest)), apply_authorized=True)

    def test_fresh_plan_guard_normal_locks_and_matching_final_report(self):
        store = MemoryStore()
        ttl.register(store, self.bound)
        result = ttl.execute(self.bound, ttl.trigger(self.bound), store, self.adapter(), now=NOW.replace(hour=13))
        self.assertEqual(result["outcome"], "complete")
        self.assertTrue(all(command[0] == "terraform" for command in self.commands))
        apply = next(command for command in self.commands if "apply" in command)
        self.assertIn("-lock=true", apply)
        self.assertNotIn("-auto-approve", apply)

    def test_replacement_unreviewed_state_and_unverified_source_cannot_apply(self):
        adapter = self.adapter()
        adapter.verify(self.bound)
        self.row["values"]["id"] = "i-00000000000000009"
        plan = adapter.plan(self.bound["modules"][0], lock_wait_seconds=60)
        with self.assertRaises(wc.ConfigError):
            adapter.validate(self.bound["modules"][0], plan)
        self.assertFalse(any("apply" in command for command in self.commands))

    def test_runner_requires_and_binds_refresh_proof_for_missing_resource(self):
        self.plan = {"resource_changes": []}
        self.refresh_proof = {"resource_changes": [], "resource_drift": [{"mode": "managed", "address": self.row["address"],
            "change": {"actions": ["delete"], "before": self.row["values"], "after": None}}]}
        adapter = self.adapter()
        adapter.verify(self.bound)
        plan = adapter.plan(self.bound['modules'][0], lock_wait_seconds=60)
        adapter.validate(self.bound['modules'][0], plan)
        self.assertTrue(any('-refresh-only' in command for command in self.commands))
        adapter.plans['pilot']['refresh_path'].write_bytes(b'tampered')
        with self.assertRaisesRegex(wc.ConfigError, 'refresh proof changed'):
            adapter.apply(self.bound['modules'][0], plan, timeout=60)

    def test_wrong_role_and_source_tree_are_rejected_before_commands(self):
        adapter = self.adapter()
        adapter.identity["Arn"] = "arn:aws:sts::000000000000:assumed-role/other/fixture"
        with self.assertRaises(wc.ConfigError):
            adapter.verify(self.bound)
        self.assertEqual(self.commands, [])
        adapter = self.adapter()
        (self.module / "main.tf").write_text("changed")
        with self.assertRaises(wc.ConfigError):
            adapter.verify(self.bound)


class PolicyTests(unittest.TestCase):
    def test_dynamic_route53_change_status_is_read_only(self):
        arn = 'arn:aws:route53:::change/*'
        args = {'account_id': '000000000000', 'region': 'us-east-1', 'state_bucket': 'foundation-state-example', 'event_id': 'class-a'}
        policy = {'Version': '2012-10-17', 'Statement': [{'Effect': 'Allow', 'Action': 'route53:GetChange', 'Resource': arn}]}
        self.assertTrue(control.validate_policy('runner', policy, [arn], **args))
        for action in ('route53:ChangeResourceRecordSets', 'route53:GetHostedZone'):
            bad = copy.deepcopy(policy)
            bad['Statement'][0]['Action'] = action
            with self.assertRaises(wc.ConfigError):
                control.validate_policy('runner', bad, [arn], **args)

    def test_dns_cleanup_is_limited_to_a_records_and_checks_missing_types(self):
        arn = 'arn:aws:route53:::hostedzone/ZEXAMPLE'
        name = 'class-a-alice.workshops.example.com'
        conditions = {'ForAllValues:StringEquals': {
            'route53:ChangeResourceRecordSetsActions': ['DELETE'],
            'route53:ChangeResourceRecordSetsRecordTypes': ['A'],
            'route53:ChangeResourceRecordSetsNormalizedRecordNames': [name]},
            'Null': {'route53:ChangeResourceRecordSetsActions': 'false',
                     'route53:ChangeResourceRecordSetsRecordTypes': 'false',
                     'route53:ChangeResourceRecordSetsNormalizedRecordNames': 'false'}}
        row = {'Effect': 'Allow', 'Action': 'route53:ChangeResourceRecordSets', 'Resource': arn, 'Condition': conditions}
        policy = {'Version': '2012-10-17', 'Statement': [row]}
        args = {'account_id': '000000000000', 'region': 'us-east-1', 'state_bucket': 'foundation-state-example',
                'event_id': 'class-a', 'approved_dns_names': [name]}
        self.assertTrue(control.validate_policy('runner', policy, [arn], **args))
        for mutation in ('missing_guard', 'wrong_type'):
            bad = copy.deepcopy(policy)
            condition = bad['Statement'][0]['Condition']
            if mutation == 'missing_guard':
                condition['Null'].pop('route53:ChangeResourceRecordSetsRecordTypes')
            else:
                condition['ForAllValues:StringEquals']['route53:ChangeResourceRecordSetsRecordTypes'] = ['AAAA']
            with self.assertRaises(wc.ConfigError):
                control.validate_policy('runner', bad, [arn], **args)

    def test_mock_policy_is_read_only_and_mutations_need_exact_reviewed_scope(self):
        policy = json.loads((wc.SPEC / "terraform/ttl-control/tests/fixtures/read-only-policy.json").read_text())
        arn = "arn:aws:s3:::example-control/control.json"
        args = {"account_id": "000000000000", "region": "us-east-1", "state_bucket": "foundation-state-example"}
        self.assertTrue(control.validate_policy("runner", policy, [arn], **args))
        for action, resource in (("iam:PutRolePolicy", "arn:aws:iam::000000000000:role/anything"),
                ("ec2:TerminateInstances", "*"), ("secretsmanager:GetSecretValue", "*"), ("s3:DeleteObject", "arn:aws:s3:::foundation-state-example/control.json")):
            bad = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Action": action, "Resource": resource}]}
            with self.assertRaises(wc.ConfigError):
                control.validate_policy("runner", bad, [resource] if resource != "*" else [arn], **args)

    def test_tf_action_verifier_matches_python_and_retirement_requires_terminal_evidence(self):
        catalog = json.loads((wc.SPEC / "ttl/policy-actions.json").read_text())
        self.assertEqual(catalog, {name: sorted(actions) for name, actions in control.MUTATIONS.items()})
        evidence = dict(schedules_disabled=True, executions_terminal=True, tasks_stopped=True, dlq_reviewed=True)
        self.assertTrue(control.retirement({"state": "complete"}, **evidence)["may_plan_retirement"])
        self.assertFalse(control.retirement({"state": "unknown"}, **evidence)["may_plan_retirement"])
        self.assertEqual(json.loads((wc.SPEC / "ttl/global-read-actions.json").read_text()), sorted(control.GLOBAL_READS))

    def test_passrole_and_dynamic_tasks_stay_in_the_cleanup_cluster(self):
        args = {"account_id": "000000000000", "region": "us-east-1", "state_bucket": "foundation-state-example", "event_id": "class-a"}
        arn = "arn:aws:iam::000000000000:role/administrator"
        row = {"Effect": "Allow", "Action": "iam:PassRole", "Resource": arn,
            "Condition": {"StringEquals": {"iam:PassedToService": "ecs-tasks.amazonaws.com"}}}
        with self.assertRaises(wc.ConfigError):
            control.validate_policy("workflow", {"Version": "2012-10-17", "Statement": [row]}, [arn], **args)
        arn = "arn:aws:ecs:us-east-1:000000000000:task/doris-ttl-class-a/*"
        row = {"Effect": "Allow", "Action": "ecs:StopTask", "Resource": arn,
            "Condition": {"ArnEquals": {"ecs:cluster": "arn:aws:ecs:us-east-1:000000000000:cluster/doris-ttl-class-a"}}}
        policy = {"Version": "2012-10-17", "Statement": [row]}
        self.assertTrue(control.validate_policy("workflow", policy, [arn], **args))
        row["Condition"]["ArnEquals"]["ecs:cluster"] += "-other"
        with self.assertRaises(wc.ConfigError):
            control.validate_policy("workflow", policy, [arn], **args)

    def test_control_resources_have_separate_owned_retirement_scope(self):
        target = binding()["target"]
        tags = {"Project": "doris-workshop", "Scope": "cleanup-control", "Event": target["event_id"], "Owner": target["owner_id"]}
        rows = [{"address": "aws_iam_role.control", "type": "aws_iam_role", "values": {"id": "cleanup", "name": "cleanup", "tags_all": tags}},
            {"address": "aws_iam_role_policy.control", "type": "aws_iam_role_policy", "values": {"id": "policy", "role": "cleanup"}}]
        self.assertTrue(all(row["retained_from_activity_cleanup"] for row in control.state_resources(rows, target)))
        rows[1]["values"]["role"] = "another-activity"
        with self.assertRaises(wc.ConfigError):
            control.state_resources(rows, target)
        rows[0]["values"]["tags_all"] = {**tags, "Scope": "activity"}
        with self.assertRaises(wc.ConfigError):
            control.state_resources(rows[:1], target)


class EntryPointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import importlib.util
        spec = importlib.util.spec_from_file_location("ttl_entry_fixture", wc.SPEC / "ttl/runner_entry.py")
        cls.entry = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.entry)

    def test_private_json_hashes_and_runtime_downloads_are_bound(self):
        import hashlib
        b = binding()
        payload = b'{"schema_version":1}'
        b["runtime_artifacts"] = [{"name": "runtime.json", "size": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}]
        client = mock.Mock()
        client.get_object.side_effect = lambda **kwargs: {"Body": io.BytesIO(payload)}
        self.assertEqual(self.entry.read_json(client, b["control"]["bucket"], "private", "000000000000",
            wc.canonical_hash(json.loads(payload))), {"schema_version": 1})
        with tempfile.TemporaryDirectory() as temporary:
            self.entry.materialize_runtime(client, b, "fixed/", temporary)
            self.assertEqual((Path(temporary) / "runtime.json").read_bytes(), payload)
        self.assertEqual(client.get_object.call_args.kwargs, {"Bucket": b["control"]["bucket"],
            "Key": "fixed/runtime/runtime.json", "ExpectedBucketOwner": "000000000000"})

    def test_changed_or_oversized_objects_cannot_enter_the_runner(self):
        b = binding()
        b["runtime_artifacts"] = [{"name": "runtime.json", "size": 1, "sha256": "a" * 64}]
        for payload in (b"x", b"xx"):
            client = mock.Mock()
            client.get_object.return_value = {"Body": io.BytesIO(payload)}
            with tempfile.TemporaryDirectory() as temporary, self.assertRaises(wc.ConfigError):
                self.entry.materialize_runtime(client, b, "fixed/", temporary)
        client.get_object.return_value = {"Body": io.BytesIO(b"{}")}
        with self.assertRaises(wc.ConfigError):
            self.entry.read_json(client, "private-example", "fixed", "000000000000", "a" * 64)


class TtlCliTests(unittest.TestCase):
    def test_offline_prepare_writes_binding_and_future_schedule_privately(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            c = fixture.config()
            c["expires_at"] = "2099-01-01T12:00:31Z"
            for name, value in (("config", c), ("manifest", fixture.manifest()), ("modules", binding()["modules"])):
                (root / (name + ".json")).write_text(json.dumps(value))
            with mock.patch("sys.stdout", new_callable=io.StringIO):
                result = ttl.main(["prepare", "--config", str(root / "config.json"), "--cleanup-manifest", str(root / "manifest.json"),
                    "--modules", str(root / "modules.json"), "--bucket", "foundation-state-example", "--revision", "1",
                    "--source-sha", "c" * 40, "--source-tree-sha256", "d" * 64, "--output", str(root / "ttl.json")])
            self.assertEqual(result, 0)
            self.assertEqual((root / "ttl.json").stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads((root / "ttl.schedule.json").read_text())["schedule_expression"], "at(2099-01-01T12:01:00)")


class S3ContractTests(unittest.TestCase):
    def setUp(self):
        import boto3
        self.client = boto3.client("s3", region_name="us-east-1", aws_access_key_id="fixture", aws_secret_access_key="fixture")
        self.binding = binding()
        self.store = ttl.S3ControlStore(self.client, self.binding["control"]["bucket"], "000000000000")

    def test_missing_object_is_distinct_from_permission_denial(self):
        from botocore.stub import Stubber
        parameters = {"Bucket": self.store.bucket, "Key": "control.json", "ExpectedBucketOwner": "000000000000"}
        with Stubber(self.client) as stub:
            stub.add_client_error("get_object", service_error_code="NoSuchKey", expected_params=parameters)
            self.assertEqual(self.store.read("control.json"), (None, None))
            stub.add_client_error("get_object", service_error_code="AccessDenied", expected_params=parameters)
            with self.assertRaises(Exception):
                self.store.read("control.json")
            stub.assert_no_pending_responses()

    def test_control_reads_close_body_and_writes_require_conditional_headers(self):
        from botocore.response import StreamingBody
        from botocore.stub import Stubber, ANY
        value = ttl.initial_record(self.binding)
        raw = json.dumps(value).encode()
        underlying = io.BytesIO(raw)
        with Stubber(self.client) as stub:
            stub.add_response("get_object", {"Body": StreamingBody(underlying, len(raw)), "ETag": '"one"'},
                {"Bucket": self.store.bucket, "Key": "control.json", "ExpectedBucketOwner": "000000000000"})
            self.assertEqual(self.store.read("control.json"), (value, '"one"'))
            self.assertTrue(underlying.closed)
            expected = {"Bucket": self.store.bucket, "Key": "control.json", "ExpectedBucketOwner": "000000000000", "Body": ANY,
                "ContentType": "application/json", "ServerSideEncryption": "AES256", "IfMatch": '"one"'}
            stub.add_client_error("put_object", service_error_code="PreconditionFailed", expected_params=expected)
            with self.assertRaises(ttl.Conflict):
                self.store.compare_set("control.json", '"one"', value)
            stub.assert_no_pending_responses()

    def test_binding_download_is_versioned_and_hash_bound(self):
        from botocore.response import StreamingBody
        from botocore.stub import Stubber
        raw = json.dumps(self.binding).encode()
        key = self.binding["control"]["key"].replace("control.json", "bindings/1.json")
        with Stubber(self.client) as stub:
            stub.add_response("get_object", {"Body": StreamingBody(io.BytesIO(raw), len(raw))}, {"Bucket": self.store.bucket,
                "Key": key, "VersionId": "immutable-version", "ExpectedBucketOwner": "000000000000"})
            result = runner.read_binding(self.client, bucket=self.store.bucket, key=key, version="immutable-version",
                expected_hash=ttl.validate_binding(self.binding), account="000000000000")
            self.assertEqual(result, self.binding)
            stub.assert_no_pending_responses()

    def test_task_role_path_never_uses_operator_or_ambient_credentials(self):
        with mock.patch.dict("os.environ", {"AWS_ACCESS_KEY_ID": "fixture"}, clear=True):
            with self.assertRaises(wc.ConfigError):
                runner.task_session(self.binding)
        with mock.patch.dict("os.environ", {"AWS_CONTAINER_CREDENTIALS_RELATIVE_URI": "/v2/credentials/fixture"}, clear=True), mock.patch("boto3.Session") as session:
            session.return_value.get_credentials.return_value.method = "container-role"
            self.assertIs(runner.task_session(self.binding), session.return_value)


if __name__ == "__main__":
    unittest.main()
