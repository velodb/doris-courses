import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import workshop_cleanup as cleanup
import workshop_config as wc
from workshop_scan import Scanner

ACCOUNT = "000000000000"
INSTANCE = "i-00000000000000001"
REGION = "us-east-1"
DOMAIN = "workshops.example.com"
ALB = f"arn:aws:elasticloadbalancing:{REGION}:{ACCOUNT}:loadbalancer/app/shared/0123456789abcdef"
TG = f"arn:aws:elasticloadbalancing:{REGION}:{ACCOUNT}:targetgroup/alice/0123456789abcdef"


def config():
    c = wc.load_document(wc.SPEC / "examples/event.example.yaml")
    c.update(event_id="class-a", owner_id="operator", region=REGION, learner_ids=["alice"], domain=DOMAIN)
    c["image"]["region"] = REGION
    return c


def tags(scope="activity", **extra):
    return {"Project": "doris-workshop", "Owner": "operator", "Event": "class-a", "Scope": scope, **extra}


def state_row(typ="aws_instance", name="learner", identity=INSTANCE, values=None, index="alice"):
    result = {"mode": "managed", "type": typ, "name": name, "address": typ + "." + name,
        "values": {"id": identity, "tags_all": tags(), **(values or {})}}
    if index is not None:
        result.update(index=index, address=result["address"] + "[" + json.dumps(index) + "]")
    return result


def manifest(rows=None, **kwargs):
    return cleanup.prepare(config(), [{"module": "pilot", "resources": [state_row()] if rows is None else rows}],
        state_bucket="foundation-state-example", **kwargs)


def snapshot(m):
    return {"schema_version": 1, "target": m["target"], "manifest_sha256": wc.canonical_hash(m), "checks": {kind: {"status": "complete", "resources": []} for kind in m["required_kinds"]},
        "states": {module: {"status": "complete", "managed_addresses": []} for module in m["modules"]}, "published_snapshots": []}


def shared():
    return {"account_id": ACCOUNT, "region": REGION, "domain": DOMAIN, "alb_arn": ALB,
        "https_listener_arn": ALB.replace("loadbalancer/", "listener/") + "/0123456789abcdef",
        "hosted_zone_id": "ZEXAMPLE", "alb_dns_name": "shared.us-east-1.elb.amazonaws.com", "alb_zone_id": "ZALB"}


def access_manifest(rows=None, hosts=None):
    return cleanup.prepare(config(), [{"module": "pilot", "resources": [state_row()]},
        {"module": "access-bindings", "resources": rows or []}], state_bucket="foundation-state-example", shared=shared(),
        hosts=hosts or ["class-a-alice-jupyter." + DOMAIN])


class OwnershipTests(unittest.TestCase):
    def test_exact_dns_and_pool_children_have_owned_parents_without_secret_exports(self):
        host = "class-a-alice-jupyter." + DOMAIN
        record = state_row("aws_route53_record", "learner", "dns-id", {"zone_id": "ZEXAMPLE", "name": host, "type": "A",
            "alias": [{"name": shared()["alb_dns_name"], "zone_id": "ZALB"}], "tags_all": {}}, index="alice:jupyter")
        pool = state_row("aws_cognito_user_pool", "activity", "us-east-1_pool", {"tags_all": tags("activity-access")}, index=None)
        client = state_row("aws_cognito_user_pool_client", "alb", "exampleclient", {"user_pool_id": "us-east-1_pool", "client_secret": "PRIVATE-CLIENT"}, index=None)
        m = access_manifest([record, pool, client])
        self.assertIn(cleanup.ref("user_pool_clients", "us-east-1_pool/exampleclient"), m["known"])
        self.assertNotIn("PRIVATE-CLIENT", json.dumps(m))
        self.assertEqual(m["cleanup_order"], ["access-bindings", "pilot"])
        s = snapshot(m)
        s["checks"]["dns_records"]["resources"] = [{"id": "ZEXAMPLE/" + host + "/A", "fields": {
            "alias_dns_name": shared()["alb_dns_name"], "alias_zone_id": "ZALB"}}]
        report = cleanup.reconcile(m, s)
        self.assertEqual(report["residual"][0]["kind"], "dns_records")
        self.assertEqual(report["conflicts"], [])
        s["checks"]["dns_records"]["resources"][0]["fields"]["alias_zone_id"] = "ZFOREIGN"
        self.assertTrue(cleanup.reconcile(m, s)["conflicts"])

    def test_shared_root_and_other_partner_hosts_cannot_be_activity_records(self):
        for host in (DOMAIN, "class-a-alice-jupyter.partner-b." + DOMAIN, "other-alice-jupyter." + DOMAIN):
            with self.assertRaises(wc.ConfigError):
                access_manifest(hosts=[host])

    def test_projection_retains_resource_ids_without_state_secrets(self):
        row = state_row(values={"user_data": "PRIVATE-STATE", "root_block_device": [{"volume_id": "vol-00000000000000001"}]})
        m = manifest([row], history={"event_id": "class-a", "known_instances": ["i-00000000000000002"],
            "known_volumes": ["vol-00000000000000002"], "known_network_interfaces": {"eni-00000000000000002": INSTANCE}})
        self.assertNotIn("PRIVATE-STATE", json.dumps(m))
        self.assertIn(cleanup.ref("instances", "i-00000000000000002"), m["known"])
        self.assertIn(cleanup.ref("volumes", "vol-00000000000000001"), m["known"])
        self.assertIn(cleanup.ref("network_interfaces", "eni-00000000000000002"), m["known"])

    def test_missing_wrong_or_shared_state_tags_and_unknown_learners_are_rejected(self):
        for resource_tags, learner in (({}, "alice"), (tags(Owner="someone-else"), "alice"),
                (tags("shared-portal"), "alice"), (tags(), "bob")):
            with self.subTest(tags=resource_tags, learner=learner), self.assertRaises(wc.ConfigError):
                manifest([state_row(values={"tags_all": resource_tags}, index=learner)])

    def test_foundation_and_tagless_foreign_parent_are_never_owned(self):
        for rows in ([state_row("aws_s3_bucket", "runtime", "foundation-state-example", index=None)],
                [state_row("aws_s3_bucket_policy", "runtime", "policy", {"bucket": "foundation-state-example", "tags_all": {}}, index=None)],
                [state_row("aws_iam_role_policy", "learner", "policy", {"role": "enterprise-role", "tags_all": {}}, index=None)]):
            with self.assertRaises(wc.ConfigError):
                manifest(rows)
        rows = [state_row("aws_s3_bucket", "runtime", "owned-runtime-example", index=None),
            state_row("aws_s3_bucket_policy", "runtime", "policy", {"bucket": "owned-runtime-example", "tags_all": {}, "policy": "PRIVATE-POLICY"}, index=None)]
        self.assertNotIn("PRIVATE-POLICY", json.dumps(manifest(rows)))
        with self.assertRaises(wc.ConfigError):
            manifest([state_row("aws_s3_bucket", "runtime", "owned-runtime-example", {"bucket": "foundation-state-example"}, index=None)])
        with self.assertRaises(wc.ConfigError):
            manifest([state_row(values={"arn": "arn:aws:ec2:us-east-1:111111111111:instance/" + INSTANCE})])

    def test_retained_claim_overlap_and_foreign_history_are_rejected(self):
        with self.assertRaises(wc.ConfigError):
            manifest(retained=[cleanup.ref("instances", INSTANCE)])
        with self.assertRaises(wc.ConfigError):
            manifest(history={"event_id": "other"})

    def test_temporary_build_ownership_is_explicit_and_published_ami_is_not_in_state(self):
        row = state_row("aws_iam_role", "builder", "temporary-builder", {"tags_all": {
            "Project": "doris-workshop", "Owner": "operator", "Scope": "image-build", "ImageBuild": "build-a"}}, index=None)
        m = cleanup.prepare(config(), [{"module": "image-build", "build_id": "build-a", "resources": [row]}], state_bucket="foundation-state-example")
        self.assertEqual(m["build_ids"], ["build-a"])
        with self.assertRaises(wc.ConfigError):
            cleanup.project_state(config(), [row], module="image-build", state_bucket="foundation-state-example")
        with self.assertRaises(wc.ConfigError):
            manifest([state_row("aws_ami", "release", "ami-00000000000000001", index=None)])

    def test_destroy_plan_requires_every_owned_delete_without_refresh_drift(self):
        row = state_row()
        valid = {"resource_changes": [{"mode": "managed", "address": row["address"], "change": {"actions": ["delete"], "before": row["values"]}}]}
        cleanup.validate_destroy_plan([row], valid)
        for mutate in (lambda p: p.update(resource_changes=[]), lambda p: p.update(complete=False),
                lambda p: p["resource_changes"][0]["change"].update(actions=["no-op"]),
                lambda p: p["resource_changes"][0]["change"]["before"].update(id="i-00000000000000002"),
                lambda p: p["resource_changes"][0]["change"]["before"]["tags_all"].update(Owner="other")):
            broken = copy.deepcopy(valid)
            mutate(broken)
            with self.assertRaises(wc.ConfigError):
                cleanup.validate_destroy_plan([row], broken)

    def test_explicit_refresh_deletion_proof_allows_retry_without_another_delete(self):
        row = state_row()
        drift = {"mode": "managed", "address": row["address"], "change": {"actions": ["delete"], "before": row["values"], "after": None}}
        self.assertEqual(cleanup.validate_destroy_plan([row], {"resource_drift": [drift]}), {row["address"]})
        with self.assertRaises(wc.ConfigError):
            cleanup.validate_destroy_plan([row], {"resource_drift": [{**drift, "change": {**drift["change"], "actions": ["update"]}}]})

    def test_separate_refresh_only_proves_an_omitted_destroy_resource(self):
        row = state_row()
        plan = {"resource_changes": [], "resource_drift": []}
        drift = {"mode": "managed", "address": row["address"],
                 "change": {"actions": ["delete"], "before": row["values"], "after": None}}
        proof = {"complete": True, "resource_changes": [], "resource_drift": [drift]}
        self.assertEqual(cleanup.validate_destroy_plan([row], plan, proof), {row["address"]})
        for change in (lambda p: p.update(complete=False),
                       lambda p: p['resource_drift'][0]['change']['before'].update(id='i-00000000000000002'),
                       lambda p: p['resource_drift'][0]['change'].update(after={'id': row['values']['id']}),
                       lambda p: p.update(resource_changes=[{'mode': 'managed', 'change': {'actions': ['delete']}}])):
            broken = copy.deepcopy(proof)
            change(broken)
            with self.assertRaises(wc.ConfigError):
                cleanup.validate_destroy_plan([row], plan, broken)


class ReconciliationTests(unittest.TestCase):
    def test_unpublished_build_images_and_key_pairs_are_residual_not_releases(self):
        m = cleanup.prepare(config(), [{"module": "image-build", "build_id": "build-a", "resources": []}], state_bucket="foundation-state-example")
        s = snapshot(m)
        build_tags = {"Project": "doris-workshop", "Owner": "operator", "ImageBuild": "build-a", "Scope": "image", "ImageStatus": "candidate"}
        s["checks"]["images"]["resources"] = [{"id": "ami-00000000000000001", "tags": build_tags}]
        s["checks"]["key_pairs"]["resources"] = [{"id": "key-00000000000000001", "tags": {**build_tags, "Scope": "image-build"}}]
        report = cleanup.reconcile(m, s)
        self.assertFalse(report["ok"])
        self.assertEqual({row["kind"] for row in report["residual"]}, {"images", "key_pairs"})
    def test_snapshot_is_bound_to_the_exact_manifest_not_just_the_event(self):
        m = manifest()
        s = snapshot(m)
        changed = copy.deepcopy(m)
        changed["known"].append(cleanup.ref("volumes", "vol-00000000000000009"))
        with self.assertRaisesRegex(wc.ConfigError, "different ownership manifest"):
            cleanup.reconcile(changed, s)
    def test_empty_state_and_complete_absence_are_point_in_time_not_global_zero(self):
        m = manifest()
        report = cleanup.reconcile(m, snapshot(m))
        self.assertTrue(report["ok"])
        self.assertFalse(report["account_globally_empty"])
        self.assertFalse(report["mutations_performed"])
        self.assertIn(cleanup.ref("instances", INSTANCE), report["known_absent"])

    def test_empty_state_does_not_hide_tagged_or_untracked_cleanup_resources(self):
        m = manifest([])
        s = snapshot(m)
        s["checks"]["tagged_resources"]["resources"].append({"id": "arn:aws:lambda:us-east-1:000000000000:function:ttl-orphan",
            "tags": tags("activity-cleanup")})
        report = cleanup.reconcile(m, s)
        self.assertFalse(report["ok"])
        self.assertFalse(report["residual"][0]["state_tracked"])

    def test_tag_index_discrepancy_requires_exact_known_native_absence(self):
        m = manifest()
        s = snapshot(m)
        arn = f'arn:aws:ec2:{REGION}:{ACCOUNT}:instance/{INSTANCE}'
        s['checks']['tagged_resources']['resources'].append({'id': arn, 'tags': tags()})
        s['tagging_discrepancies'] = [{'arn': arn, 'native': cleanup.ref('instances', INSTANCE), 'result': 'terminated'}]
        report = cleanup.reconcile(m, s)
        self.assertTrue(report['ok'])
        self.assertEqual(report['tagging_discrepancies'][0]['native_result'], 'terminated')
        self.assertEqual(s['checks']['tagged_resources']['resources'][0]['id'], arn)
        s['checks']['instances']['status'] = 'failed'
        self.assertFalse(cleanup.reconcile(m, s)['ok'])
        s['checks']['instances']['status'] = 'complete'
        s['checks']['instances']['resources'].append({'id': INSTANCE, 'tags': tags()})
        self.assertFalse(cleanup.reconcile(m, s)['ok'])
        s['checks']['instances']['resources'].clear()
        s['tagging_discrepancies'][0]['native']['id'] = 'i-00000000000000002'
        with self.assertRaises(wc.ConfigError):
            cleanup.reconcile(m, s)

    def test_missing_family_or_state_proof_and_stale_state_block_success(self):
        m = manifest()
        for mutate in (lambda s: s["checks"].pop("volumes"), lambda s: s.update(states={}),
                lambda s: s["states"]["pilot"].update(managed_addresses=[state_row()["address"]])):
            s = snapshot(m)
            mutate(s)
            self.assertFalse(cleanup.reconcile(m, s)["ok"])
        broken = copy.deepcopy(m)
        broken["required_kinds"] = []
        with self.assertRaises(wc.ConfigError):
            cleanup.reconcile(broken, snapshot(m))

    def test_query_failure_is_not_absence_and_raw_error_is_not_reported(self):
        m = manifest()
        s = snapshot(m)
        s["checks"]["instances"] = {"status": "failed", "error": "PRIVATE-AWS-MESSAGE", "resources": []}
        report = cleanup.reconcile(m, s)
        self.assertFalse(report["ok"])
        self.assertNotIn(cleanup.ref("instances", INSTANCE), report["known_absent"])
        self.assertNotIn("PRIVATE-AWS-MESSAGE", json.dumps(report))

    def test_exact_history_is_not_a_delete_grant_after_tags_change(self):
        m = manifest()
        s = snapshot(m)
        s["checks"]["instances"]["resources"].append({"id": INSTANCE, "tags": tags(Owner="other")})
        report = cleanup.reconcile(m, s)
        self.assertFalse(report["ok"])
        self.assertEqual(len(report["conflicts"]), 1)

    def test_shared_other_activity_and_published_snapshots_are_retained(self):
        m = manifest([])
        s = snapshot(m)
        s["checks"]["instances"]["resources"] = [{"id": INSTANCE, "tags": tags("shared-portal")},
            {"id": "i-00000000000000002", "tags": tags(Event="other-class")}]
        s["checks"]["snapshots"]["resources"] = [{"id": "snap-00000000000000001", "tags": tags()}]
        s["published_snapshots"] = ["snap-00000000000000001"]
        report = cleanup.reconcile(m, s)
        self.assertTrue(report["ok"])
        self.assertEqual(len(report["retained"]), 3)
        self.assertTrue(all(row["cost_review_required"] for row in report["retained"]))

    def test_partial_results_eventual_consistency_and_repeated_report(self):
        m = manifest()
        s = snapshot(m)
        s["checks"]["instances"]["resources"] = [{"id": INSTANCE, "tags": tags()}]
        self.assertFalse(cleanup.reconcile(m, s)["ok"])
        s["checks"]["instances"]["resources"].clear()
        self.assertEqual(cleanup.reconcile(m, s), cleanup.reconcile(m, copy.deepcopy(s)))
        self.assertTrue(cleanup.reconcile(m, s)["ok"])


class ScannerTests(unittest.TestCase):
    def test_unsupported_pagination_and_request_budget_fail_closed(self):
        aws = mock.Mock()
        aws.client.return_value.can_paginate.return_value = False
        aws.client.return_value.describe_instances.return_value = {"Reservations": [], "NextToken": "not-exhausted"}
        scanner = Scanner(aws, manifest())
        scanner.run("instances", lambda: scanner.ec2("instances"))
        self.assertEqual(scanner.snapshot["checks"]["instances"]["status"], "failed")
        scanner = Scanner(aws, manifest(), max_calls=0)
        with self.assertRaises(wc.ConfigError):
            scanner.call("ec2", "describe_instances")
    def test_failed_family_is_preserved_and_no_aws_message_escapes(self):
        scanner = Scanner(mock.Mock(), manifest())
        scanner.run("volumes", lambda: (_ for _ in ()).throw(RuntimeError("PRIVATE-RESPONSE")))
        self.assertEqual(scanner.snapshot["checks"]["volumes"]["status"], "failed")
        self.assertNotIn("PRIVATE-RESPONSE", json.dumps(scanner.snapshot))

    def test_shared_vpc_is_not_an_activity_ownership_relationship(self):
        m = manifest()
        m["retained"].append(cleanup.ref("vpcs", "vpc-00000000000000001"))
        scanner = Scanner(mock.Mock(), m)
        scanner.snapshot["checks"]["instances"] = {"status": "complete", "resources": []}
        scanner.add("instances", "i-00000000000000002", resource_tags=tags(Event="other-class"), fields={"vpc_id": "vpc-00000000000000001"})
        row = scanner.snapshot["checks"]["instances"]["resources"][0]
        self.assertFalse(row["related"])
        scanner.add("instances", "i-00000000000000003", fields={"instance_id": INSTANCE})
        self.assertTrue(scanner.snapshot["checks"]["instances"]["resources"][1]["related"])

    def test_read_only_api_gate_and_wrong_account_fail_before_resource_queries(self):
        aws = mock.Mock()
        aws.client.return_value.get_caller_identity.return_value = {"Account": "111111111111", "Arn": "arn:aws:iam::111111111111:user/operator"}
        scanner = Scanner(aws, manifest())
        with self.assertRaises(wc.ConfigError):
            scanner.call("ec2", "terminate_instances")
        with self.assertRaises(wc.ConfigError):
            scanner.collect()
        self.assertEqual(aws.client.call_count, 1)


class CleanupCliTests(unittest.TestCase):
    def test_offline_prepare_and_report_use_private_outputs_without_secrets_or_cloud(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config.json").write_text(json.dumps(config()))
            export = {"format_version": "1.0", "values": {"root_module": {"resources": [state_row(values={"user_data": "PRIVATE-STATE"})]}}}
            (root / "state.json").write_text(json.dumps(export))
            with mock.patch("sys.stdout", new_callable=io.StringIO):
                code = cleanup.main(["prepare", "--config", str(root / "config.json"), "--state", "pilot=" + str(root / "state.json"),
                    "--state-bucket", "foundation-state-example", "--output", str(root / "manifest.json")])
            self.assertEqual(code, 0)
            m = json.loads((root / "manifest.json").read_text())
            self.assertNotIn("PRIVATE-STATE", json.dumps(m))
            (root / "scan.json").write_text(json.dumps(snapshot(m)))
            with mock.patch("sys.stdout", new_callable=io.StringIO):
                code = cleanup.main(["report", "--manifest", str(root / "manifest.json"), "--snapshot", str(root / "scan.json"), "--output", str(root / "report.json")])
            self.assertEqual(code, 0)
            self.assertTrue(json.loads((root / "report.json").read_text())["ok"])
            self.assertEqual((root / "report.json").stat().st_mode & 0o777, 0o600)
            with self.assertRaises(wc.ConfigError):
                cleanup.state_export("pilot", {})


class SdkScanTests(unittest.TestCase):
    def setUp(self):
        import boto3
        self.clients = {}
        self.session = boto3.Session(region_name=REGION, aws_access_key_id="fixture", aws_secret_access_key="fixture")
        self.aws = mock.Mock()
        def client(service):
            if service not in self.clients:
                self.clients[service] = self.session.client(service)
            return self.clients[service]
        self.aws.client.side_effect = client

    def test_iam_disappearing_role_is_absent_with_modeled_exception(self):
        from botocore.stub import Stubber
        from datetime import datetime, timezone
        client = self.session.client("iam")
        self.clients["iam"] = client
        scanner = Scanner(self.aws, manifest())
        role = {"Path": "/", "RoleName": "retiring-role", "RoleId": "AROA00000000000000000",
            "Arn": "arn:aws:iam::000000000000:role/retiring-role", "CreateDate": datetime.now(timezone.utc)}
        with Stubber(client) as stub:
            stub.add_response("list_roles", {"Roles": [role], "IsTruncated": False}, {})
            stub.add_client_error("get_role", service_error_code="NoSuchEntity", expected_params={"RoleName": "retiring-role"})
            scanner.run("roles", lambda: scanner.iam("roles"))
            self.assertEqual(scanner.snapshot["checks"]["roles"], {"status": "complete", "resources": []})
            stub.assert_no_pending_responses()

    def test_ssm_document_scan_projects_tags_without_document_content(self):
        from botocore.stub import Stubber
        client = self.session.client("ssm")
        self.clients["ssm"] = client
        scanner = Scanner(self.aws, manifest())
        with Stubber(client) as stub:
            stub.add_response("list_documents", {"DocumentIdentifiers": [{"Name": "owned-installer", "DocumentType": "Command"}]},
                {"Filters": [{"Key": "Owner", "Values": ["Self"]}]})
            stub.add_response("list_tags_for_resource", {"TagList": [{"Key": k, "Value": v} for k, v in tags().items()]},
                {"ResourceType": "Document", "ResourceId": "owned-installer"})
            scanner.run("ssm_documents", scanner.ssm)
            self.assertEqual(scanner.snapshot["checks"]["ssm_documents"]["status"], "complete")
            self.assertEqual(scanner.snapshot["checks"]["ssm_documents"]["resources"][0]["id"], "owned-installer")
            stub.assert_no_pending_responses()

    def test_tagged_inventory_reads_all_pages_and_keeps_unknown_cleanup_types(self):
        from botocore.stub import Stubber
        client = self.session.client("resourcegroupstaggingapi")
        self.clients["resourcegroupstaggingapi"] = client
        m = manifest([])
        scanner = Scanner(self.aws, m)
        filters = [{"Key": "Project", "Values": ["doris-workshop"]}]
        with Stubber(client) as stub:
            stub.add_response("get_resources", {"ResourceTagMappingList": [], "PaginationToken": "more"}, {"TagFilters": filters})
            stub.add_response("get_resources", {"ResourceTagMappingList": [{"ResourceARN": "arn:aws:lambda:us-east-1:000000000000:function:cleanup",
                "Tags": [{"Key": k, "Value": v} for k, v in tags("activity-cleanup").items()]}]}, {"TagFilters": filters, "PaginationToken": "more"})
            scanner.run("tagged_resources", scanner.tagged)
            self.assertEqual(scanner.snapshot["checks"]["tagged_resources"]["status"], "complete")
            s = snapshot(m)
            s["checks"]["tagged_resources"] = scanner.snapshot["checks"]["tagged_resources"]
            self.assertFalse(cleanup.reconcile(m, s)["ok"])
            stub.assert_no_pending_responses()

    def test_tagging_discrepancy_uses_exact_native_query_and_fails_closed(self):
        from botocore.stub import Stubber
        client = self.session.client('ec2')
        self.clients['ec2'] = client
        scanner = Scanner(self.aws, manifest())
        arn = f'arn:aws:ec2:{REGION}:{ACCOUNT}:instance/{INSTANCE}'
        scanner.snapshot['checks']['instances'] = {'status': 'complete', 'resources': []}
        scanner.snapshot['checks']['tagged_resources'] = {'status': 'complete', 'resources': [{'id': arn, 'tags': tags()}]}
        with Stubber(client) as stub:
            stub.add_client_error('describe_instances', service_error_code='InvalidInstanceID.NotFound',
                                  expected_params={'InstanceIds': [INSTANCE]})
            scanner.confirm_tagging_discrepancies()
            self.assertEqual(scanner.snapshot['tagging_discrepancies'][0]['result'], 'not-found')
            stub.add_client_error('describe_instances', service_error_code='UnauthorizedOperation',
                                  expected_params={'InstanceIds': [INSTANCE]})
            scanner.confirm_tagging_discrepancies()
            self.assertEqual(scanner.snapshot['tagging_discrepancies'], [])
            stub.assert_no_pending_responses()

    def test_exact_volume_and_cognito_not_found_can_confirm_stale_tags(self):
        from botocore.stub import Stubber
        ec2, cognito = self.session.client('ec2'), self.session.client('cognito-idp')
        self.clients.update(ec2=ec2, **{'cognito-idp': cognito})
        m = access_manifest()
        volume, pool = 'vol-00000000000000001', 'us-east-1_abc123'
        m['known'].extend([cleanup.ref('volumes', volume), cleanup.ref('user_pools', pool)])
        scanner = Scanner(self.aws, m)
        scanner.snapshot['checks']['volumes'] = {'status': 'complete', 'resources': []}
        scanner.snapshot['checks']['user_pools'] = {'status': 'complete', 'resources': []}
        scanner.snapshot['checks']['tagged_resources'] = {'status': 'complete', 'resources': [
            {'id': f'arn:aws:ec2:{REGION}:{ACCOUNT}:volume/{volume}', 'tags': tags()},
            {'id': f'arn:aws:cognito-idp:{REGION}:{ACCOUNT}:userpool/{pool}', 'tags': tags('activity-access')}]}
        with Stubber(ec2) as ec2_stub, Stubber(cognito) as cognito_stub:
            ec2_stub.add_client_error('describe_volumes', service_error_code='InvalidVolume.NotFound', expected_params={'VolumeIds': [volume]})
            cognito_stub.add_client_error('describe_user_pool', service_error_code='ResourceNotFoundException', expected_params={'UserPoolId': pool})
            scanner.confirm_tagging_discrepancies()
            self.assertEqual([p['result'] for p in scanner.snapshot['tagging_discrepancies']], ['not-found', 'not-found'])
            ec2_stub.assert_no_pending_responses()
            cognito_stub.assert_no_pending_responses()

    def test_foreign_target_group_attachment_is_reported_not_deregistered(self):
        from botocore.stub import Stubber
        client = self.session.client("elbv2")
        self.clients["elbv2"] = client
        m = access_manifest()
        scanner = Scanner(self.aws, m)
        with Stubber(client) as stub:
            stub.add_response("describe_target_groups", {"TargetGroups": [{"TargetGroupArn": TG}]}, {})
            stub.add_response("describe_tags", {"TagDescriptions": [{"ResourceArn": TG, "Tags": [{"Key": k, "Value": v} for k, v in tags("activity-access", Event="other-class").items()]}]}, {"ResourceArns": [TG]})
            stub.add_response("describe_target_health", {"TargetHealthDescriptions": [{"Target": {"Id": INSTANCE, "Port": 8080}, "TargetHealth": {"State": "healthy"}}]}, {"TargetGroupArn": TG})
            scanner.run("target_attachments", scanner.targets)
            self.assertEqual(scanner.snapshot["checks"]["target_attachments"]["status"], "complete")
            s = snapshot(m)
            s["checks"]["target_attachments"] = scanner.snapshot["checks"]["target_attachments"]
            self.assertTrue(cleanup.reconcile(m, s)["conflicts"])
            stub.assert_no_pending_responses()

    def test_untagged_listener_rule_with_exact_activity_host_is_blocked(self):
        from botocore.stub import Stubber
        client = self.session.client("elbv2")
        self.clients["elbv2"] = client
        m = access_manifest()
        scanner = Scanner(self.aws, m)
        listener = shared()["https_listener_arn"]
        rule = listener.replace(":listener/", ":listener-rule/") + "/0123456789abcdef"
        with Stubber(client) as stub:
            stub.add_response("describe_listeners", {"Listeners": [{"ListenerArn": listener}]}, {"LoadBalancerArn": ALB})
            stub.add_response("describe_rules", {"Rules": [{"RuleArn": rule, "IsDefault": False,
                "Conditions": [{"Field": "host-header", "Values": ["class-a-alice-jupyter." + DOMAIN]}],
                "Actions": [{"Type": "forward", "TargetGroupArn": TG}]}]}, {"ListenerArn": listener})
            stub.add_response("describe_tags", {"TagDescriptions": [{"ResourceArn": rule}]}, {"ResourceArns": [rule]})
            scanner.run("listener_rules", scanner.rules)
            self.assertEqual(scanner.snapshot["checks"]["listener_rules"]["status"], "complete")
            self.assertTrue(scanner.snapshot["checks"]["listener_rules"]["resources"][0]["related"])
            stub.assert_no_pending_responses()

    def test_dns_pages_include_changed_record_types_and_retain_the_root(self):
        from botocore.stub import Stubber
        client = self.session.client("route53")
        self.clients["route53"] = client
        m = access_manifest()
        scanner = Scanner(self.aws, m)
        host = "class-a-alice-jupyter." + DOMAIN
        with Stubber(client) as stub:
            stub.add_response("get_hosted_zone", {"HostedZone": {"Id": "/hostedzone/ZEXAMPLE", "Name": DOMAIN + ".", "CallerReference": "fixture", "Config": {"PrivateZone": False}},
                "DelegationSet": {"NameServers": ["ns.example.com"]}}, {"Id": "ZEXAMPLE"})
            stub.add_response("list_resource_record_sets", {"ResourceRecordSets": [{"Name": DOMAIN + ".", "Type": "A", "AliasTarget": {
                "DNSName": shared()["alb_dns_name"], "HostedZoneId": "ZALB", "EvaluateTargetHealth": True}}], "IsTruncated": True,
                "MaxItems": "1", "NextRecordName": host + ".", "NextRecordType": "CNAME"}, {"HostedZoneId": "ZEXAMPLE"})
            stub.add_response("list_resource_record_sets", {"ResourceRecordSets": [{"Name": host + ".", "Type": "CNAME", "TTL": 60,
                "ResourceRecords": [{"Value": "other.example.com"}]}], "IsTruncated": False, "MaxItems": "1"},
                {"HostedZoneId": "ZEXAMPLE", "StartRecordName": host + ".", "StartRecordType": "CNAME"})
            scanner.run("dns_records", scanner.dns)
            rows = scanner.snapshot["checks"]["dns_records"]["resources"]
            self.assertEqual(scanner.snapshot["checks"]["dns_records"]["status"], "complete")
            self.assertEqual(len(rows), 2)
            self.assertTrue(rows[1]["related"])
            stub.assert_no_pending_responses()

    def test_ec2_pagination_reads_second_page_and_omits_user_data(self):
        from botocore.stub import Stubber
        client = self.session.client("ec2")
        self.clients["ec2"] = client
        scanner = Scanner(self.aws, manifest())
        with Stubber(client) as stub:
            stub.add_response("describe_instances", {"Reservations": [], "NextToken": "next-page"}, {})
            stub.add_response("describe_instances", {"Reservations": [{"Instances": [{"InstanceId": INSTANCE, "State": {"Name": "running"},
                "Tags": [{"Key": k, "Value": v} for k, v in tags().items()]}]}]}, {"NextToken": "next-page"})
            scanner.run("instances", lambda: scanner.ec2("instances"))
            self.assertEqual(scanner.snapshot["checks"]["instances"]["status"], "complete")
            self.assertEqual(scanner.snapshot["checks"]["instances"]["resources"][0]["id"], INSTANCE)
            stub.assert_no_pending_responses()

    def test_ec2_permission_failure_never_becomes_complete_empty(self):
        from botocore.stub import Stubber
        client = self.session.client("ec2")
        self.clients["ec2"] = client
        scanner = Scanner(self.aws, manifest())
        with Stubber(client) as stub:
            stub.add_client_error("describe_volumes", service_error_code="UnauthorizedOperation", service_message="PRIVATE-AWS-MESSAGE", expected_params={})
            scanner.run("volumes", lambda: scanner.ec2("volumes"))
            self.assertEqual(scanner.snapshot["checks"]["volumes"]["status"], "failed")
            self.assertNotIn("PRIVATE-AWS-MESSAGE", json.dumps(scanner.snapshot))
            stub.assert_no_pending_responses()

    def test_cognito_domain_is_checked_even_after_pool_list_is_empty(self):
        from botocore.stub import Stubber
        client = self.session.client("cognito-idp")
        self.clients["cognito-idp"] = client
        m = access_manifest()
        m["known"].append(cleanup.ref("user_pool_domains", "retiring-domain"))
        scanner = Scanner(self.aws, m)
        with Stubber(client) as stub:
            stub.add_response("list_user_pools", {"UserPools": []}, {"MaxResults": 60})
            stub.add_response("describe_user_pool_domain", {"DomainDescription": {"Domain": "retiring-domain", "UserPoolId": "us-east-1_pool"}}, {"Domain": "retiring-domain"})
            scanner.run("user_pool_domains", lambda: scanner.pools("user_pool_domains"))
            self.assertEqual(scanner.snapshot["checks"]["user_pool_domains"]["resources"][0]["id"], "retiring-domain")
            stub.assert_no_pending_responses()

    def test_cognito_child_inventory_does_not_read_other_activity_clients(self):
        from botocore.stub import Stubber
        client = self.session.client("cognito-idp")
        self.clients["cognito-idp"] = client
        scanner = Scanner(self.aws, access_manifest())
        owned, other = 'us-east-1_owned', 'us-east-1_other'
        arn = lambda pool: f'arn:aws:cognito-idp:{REGION}:{ACCOUNT}:userpool/{pool}'
        with Stubber(client) as stub:
            stub.add_response('list_user_pools', {'UserPools': [{'Id': owned, 'Name': 'owned'}, {'Id': other, 'Name': 'other'}]}, {'MaxResults': 60})
            stub.add_response('list_tags_for_resource', {'Tags': tags('activity-access')}, {'ResourceArn': arn(owned)})
            stub.add_response('list_user_pool_clients', {'UserPoolClients': [{'ClientId': 'client1', 'UserPoolId': owned}]},
                              {'UserPoolId': owned, 'MaxResults': 60})
            stub.add_response('list_tags_for_resource', {'Tags': tags('activity-access', Event='other-class')}, {'ResourceArn': arn(other)})
            scanner.run('user_pool_clients', lambda: scanner.pools('user_pool_clients'))
            self.assertEqual(scanner.snapshot['checks']['user_pool_clients']['status'], 'complete')
            self.assertEqual([r['id'] for r in scanner.snapshot['checks']['user_pool_clients']['resources']], [owned + '/client1'])
            stub.assert_no_pending_responses()

    def test_s3_no_tags_is_not_a_missing_bucket(self):
        from botocore.stub import Stubber
        client = self.session.client("s3")
        self.clients["s3"] = client
        scanner = Scanner(self.aws, manifest())
        with Stubber(client) as stub:
            stub.add_response("list_buckets", {"Buckets": [{"Name": "foundation-state-example", "BucketRegion": REGION}]}, {"BucketRegion": REGION})
            stub.add_client_error("get_bucket_tagging", service_error_code="NoSuchTagSet", expected_params={"Bucket": "foundation-state-example"})
            scanner.run("buckets", scanner.buckets)
            self.assertEqual(scanner.snapshot["checks"]["buckets"]["status"], "complete")
            self.assertEqual(scanner.snapshot["checks"]["buckets"]["resources"][0]["id"], "foundation-state-example")
            stub.assert_no_pending_responses()

    def test_s3_inaccessible_bucket_does_not_hide_later_activity_bucket(self):
        from botocore.stub import Stubber
        client = self.session.client("s3")
        self.clients["s3"] = client
        m = manifest([state_row("aws_s3_bucket", "runtime", "owned-runtime-example", index=None)])
        scanner = Scanner(self.aws, m)
        with Stubber(client) as stub:
            stub.add_response("list_buckets", {"Buckets": [{"Name": "inaccessible-example", "BucketRegion": REGION},
                {"Name": "owned-runtime-example", "BucketRegion": REGION}]}, {"BucketRegion": REGION})
            stub.add_client_error("get_bucket_tagging", service_error_code="AccessDenied",
                service_message="PRIVATE-RESPONSE", expected_params={"Bucket": "inaccessible-example"})
            stub.add_response("get_bucket_tagging", {"TagSet": [{"Key": k, "Value": v} for k, v in tags().items()]},
                {"Bucket": "owned-runtime-example"})
            scanner.run("buckets", scanner.buckets)
            check = scanner.snapshot["checks"]["buckets"]
            self.assertEqual(check["status"], "failed")
            self.assertEqual([row["id"] for row in check["resources"]], ["owned-runtime-example"])
            self.assertNotIn("PRIVATE-RESPONSE", json.dumps(check))
            s = snapshot(m)
            s["checks"]["buckets"] = check
            report = cleanup.reconcile(m, s)
            self.assertFalse(report["ok"])
            self.assertIn("buckets", report["incomplete_kinds"])
            self.assertEqual(report["residual"][0]["id"], "owned-runtime-example")
            stub.assert_no_pending_responses()

    def test_s3_unexpected_region_fails_closed(self):
        from botocore.stub import Stubber
        client = self.session.client("s3")
        self.clients["s3"] = client
        scanner = Scanner(self.aws, manifest())
        with Stubber(client) as stub:
            stub.add_response("list_buckets", {"Buckets": [{"Name": "unknown-example", "BucketRegion": "us-west-2"}]},
                {"BucketRegion": REGION})
            scanner.run("buckets", scanner.buckets)
            check = scanner.snapshot["checks"]["buckets"]
            self.assertEqual(check["status"], "failed")
            self.assertEqual(check["resources"], [])
            stub.assert_no_pending_responses()


if __name__ == "__main__":
    unittest.main()
