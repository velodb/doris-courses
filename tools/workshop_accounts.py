#!/usr/bin/env python3
"""Create administrator-invited Cognito users without sending email or exposing passwords."""
import argparse
import json
import os
from pathlib import Path
import secrets
import string
import subprocess
import sys
import tempfile

import workshop_config as wc
from workshop_access import require


def password():
    groups = [string.ascii_lowercase, string.ascii_uppercase, string.digits, "!@#%+=_-?."]
    chars = [secrets.choice(group) for group in groups]
    chars += [secrets.choice("".join(groups)) for _ in range(24)]
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


def aws_call(config, operation, parameters, *, service="cognito-idp"):
    # Secret parameters go through a private short-lived file, never argv/stdout.
    with tempfile.TemporaryDirectory(prefix="workshop-account-") as tmp:
        path = Path(tmp) / "request.json"
        wc.write_manifest(path, parameters)
        env = {k: v for k, v in os.environ.items() if not k.startswith("AWS_")}
        result = subprocess.run(["aws", "--profile", config["aws_profile"], "--region", config["region"],
            "--no-cli-pager", "--output", "json", service, operation, "--cli-input-json", "file://" + str(path)],
            env=env, capture_output=True, text=True, timeout=60)
    require(result.returncode == 0, "AWS account operation failed; no credentials were printed")
    return json.loads(result.stdout)


def create(config, identity, directory, *, call=aws_call):
    require(identity["event_id"] == config["event_id"], "identity belongs to another event")
    require(identity["issuer"] == f'https://cognito-idp.{config["region"]}.amazonaws.com/{identity["user_pool_id"]}', "issuer must match the exact Cognito pool")
    who = call(config, "get-caller-identity", {}, service="sts")
    require(who["Account"] == config["aws_account_id"], "AWS identity belongs to the wrong account")
    arn = f'arn:aws:cognito-idp:{config["region"]}:{config["aws_account_id"]}:userpool/{identity["user_pool_id"]}'
    tags = call(config, "list-tags-for-resource", {"ResourceArn": arn})["Tags"]
    require(all(tags.get(k) == value for k, value in {"Project": "doris-workshop", "Scope": "activity-access", "Event": config["event_id"], "Owner": config["owner_id"]}.items()), "refuse to create users in an unowned pool")
    require(not directory.exists(), "select a new private account delivery directory")
    directory.mkdir(mode=0o700, parents=True)
    subjects = {}
    for learner in sorted(config["learner_ids"]):
        secret = password()
        # Persist the credential privately before creating the account, so a
        # crash cannot leave an account whose generated password is lost.
        wc.write_manifest(directory / (learner + ".json"), {"event_id": config["event_id"],
            "user_pool_id": identity["user_pool_id"], "username": learner, "temporary_password": secret})
        response = call(config, "admin-create-user", {"UserPoolId": identity["user_pool_id"], "Username": learner,
            "TemporaryPassword": secret, "MessageAction": "SUPPRESS"})
        user = response["User"]
        require(user["Username"] == learner and user["UserStatus"] == "FORCE_CHANGE_PASSWORD", "unexpected Cognito account result")
        subject = next(attr["Value"] for attr in user["Attributes"] if attr["Name"] == "sub")
        subjects[learner] = [subject]
        wc.write_manifest(directory / (learner + "-created.json"), {"event_id": config["event_id"], "username": learner, "subject": subject})
    wc.write_manifest(directory / "subjects.json", subjects)
    return subjects


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("config", "identity", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    try:
        config, _, _ = wc.validate_config(wc.load_document(args.config))
        create(config, wc.load_document(args.identity), args.output_dir)
        print("Accounts created. Credentials remain in private delivery files; no messages were sent.")
    except (ValueError, KeyError, TypeError, OSError, StopIteration, subprocess.SubprocessError):
        print("accounts: operation failed; retain private delivery/receipt files for reconciliation; existing accounts were not reset", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
