#!/usr/bin/env python3
"""Scan one local TTL image and its packed provider using an offline Trivy DB."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import uuid
import zipfile


def require(value, message):
    if not value:
        raise ValueError(message)


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def database_metadata(cache, *, now=None, max_hours=24):
    value = json.loads((cache / "db/metadata.json").read_text())
    stamp = datetime.fromisoformat(value["UpdatedAt"].replace("Z", "+00:00"))
    age = ((now or datetime.now(timezone.utc)) - stamp).total_seconds()
    require(value["Version"] == 2 and -300 <= age <= max_hours * 3600, "database is missing, stale or from the future")
    return {"metadata": value, "db_sha256": digest(cache / "db/trivy.db"), "age_seconds": round(age)}


def summarize(image, provider, expected_python):
    results, packed = image.get("Results", []), provider.get("Results", [])
    operating_system = [row for row in results if row.get("Class") == "os-pkgs" and row.get("Packages")]
    python = [row for row in results if row.get("Type") == "python-pkg"]
    terraform = [row for row in results if row.get("Type") == "gobinary" and
        any(package.get("Name") == "github.com/hashicorp/terraform" for package in row.get("Packages", []))]
    aws_provider = [row for row in packed if row.get("Type") == "gobinary" and
        any(package.get("Name") == "github.com/hashicorp/terraform-provider-aws" for package in row.get("Packages", []))]
    names = {package["Name"].lower().replace("_", "-") for row in python for package in row.get("Packages", [])}
    require(operating_system and python and expected_python and names and terraform and aws_provider and set(expected_python) <= names,
        "incomplete OS, Python, Terraform or provider coverage")
    counts = Counter()
    unique = set()
    groups = []
    for scope, rows in (("image", results), ("expanded-provider", packed)):
        for row in rows:
            findings = row.get("Vulnerabilities", [])
            counts.update(item["Severity"] for item in findings)
            unique.update(item["VulnerabilityID"] for item in findings)
            groups.append({"scope": scope, "type": row.get("Type"), "packages": len(row.get("Packages", [])),
                "findings": dict(Counter(item["Severity"] for item in findings))})
    return {"scan_completed": True, "coverage": {"os": True, "python": True, "terraform": True, "expanded_provider": True},
        "package_findings": dict(counts), "unique_advisories": len(unique), "groups": groups,
        "review_required": any(counts[level] for level in ("CRITICAL", "HIGH", "UNKNOWN")), "cloud_accepted": False}


def run_scan(args):
    require(re.fullmatch(r"sha256:[0-9a-f]{64}|doris-workshop-ttl:[A-Za-z0-9_.-]+", args.image), "select a local runner image")
    require(digest(args.trivy) == args.trivy_sha256, "scanner executable differs from its reviewed hash")
    args.output.mkdir(parents=True, exist_ok=False)
    database = database_metadata(args.cache_dir)
    with tempfile.TemporaryDirectory(prefix="ttl-scan-") as temporary:
        work = Path(temporary)
        home = work / "home"
        home.mkdir()
        env = {"PATH": os.environ["PATH"], "HOME": str(home)}
        version = subprocess.check_output([str(args.trivy), "--version"], env=env, text=True)
        require(version.startswith("Version: 0.76.1"), "use the reviewed Trivy version")
        image = json.loads(subprocess.check_output(["docker", "image", "inspect", args.image], text=True))[0]
        require(image["Os"] == "linux" and image["Architecture"] == "amd64" and
            image["Config"]["Entrypoint"] == ["python", "-I", "/opt/doris-courses/workshop/aws/ttl/runner_entry.py"], "unexpected runner image")
        archive = work / "image.tar"
        subprocess.run(["docker", "save", "--output", str(archive), image["Id"]], check=True, capture_output=True)
        name = "doris-ttl-scan-" + uuid.uuid4().hex
        try:
            subprocess.run(["docker", "create", "--name", name, "--network", "none", image["Id"]], check=True, capture_output=True)
            for source, destination in (("/opt/doris-workshop/source.json", "source.json"),
                ("/opt/doris-courses/workshop/aws/ttl/wheels.json", "wheels.json"),
                ("/opt/doris-courses/workshop/aws/terraform/pilot/.terraform.lock.hcl", "provider.lock")):
                subprocess.run(["docker", "cp", name + ":" + source, str(work / destination)], check=True, capture_output=True)
            version_match = re.search(r'version\s*=\s*"([0-9.]+)"', (work / "provider.lock").read_text())
            require(version_match, "missing provider version")
            filename = "terraform-provider-aws_" + version_match[1] + "_linux_amd64.zip"
            subprocess.run(["docker", "cp", name + ":/opt/terraform-providers/registry.terraform.io/hashicorp/aws/" + filename,
                str(work / "provider.zip")], check=True, capture_output=True)
        finally:
            subprocess.run(["docker", "rm", "-f", "-v", name], capture_output=True)
        require('"zh:' + digest(work / "provider.zip") + '"' in (work / "provider.lock").read_text(), "provider ZIP does not match image lock")
        provider_root = work / "provider"
        provider_root.mkdir()
        binary = provider_root / "terraform-provider-aws"
        with zipfile.ZipFile(work / "provider.zip") as package:
            selected = [row for row in package.infolist() if row.filename.startswith("terraform-provider-aws_") and "/" not in row.filename]
            require(len(selected) == 1 and selected[0].file_size <= 2 * 1024 ** 3, "unexpected provider archive")
            with package.open(selected[0]) as source, binary.open("xb") as destination:
                while chunk := source.read(1024 ** 2):
                    destination.write(chunk)
        binary.chmod(0o700)
        common = ["--cache-dir", str(args.cache_dir), "--disable-telemetry", "--skip-version-check", "--skip-db-update",
            "--skip-java-db-update", "--skip-check-update", "--offline-scan", "--scanners", "vuln", "--list-all-pkgs",
            "--format", "json", "--ignorefile", "", "--config", "", "--timeout", "15m"]
        for kind, command in (("image", ["image", "--input", str(archive)]), ("provider", ["rootfs", str(provider_root)])):
            with (args.output / (kind + ".log")).open("w") as log:
                subprocess.run([str(args.trivy), *command, *common, "--output", str(args.output / (kind + ".json"))],
                    env=env, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=1000)
        expected = [row["package"] for row in json.loads((work / "wheels.json").read_text())["wheels"]]
        summary = summarize(json.loads((args.output / "image.json").read_text()), json.loads((args.output / "provider.json").read_text()), expected)
        summary.update(image_id=image["Id"], source=json.loads((work / "source.json").read_text()), scanner={"version": "0.76.1", "sha256": args.trivy_sha256},
            database=database, provider_zip_sha256=digest(work / "provider.zip"), provider_binary_sha256=digest(binary), image_archive_sha256=digest(archive))
        (args.output / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
        return summary


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--trivy", required=True, type=Path)
    parser.add_argument("--trivy-sha256", required=True)
    parser.add_argument("--cache-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.trivy, args.cache_dir, args.output = args.trivy.resolve(), args.cache_dir.resolve(), args.output.resolve()
    try:
        result = run_scan(args)
        print("Local scan completed; review required." if result["review_required"] else "Local scan completed; no High/Critical/Unknown findings.")
        return 2 if result["review_required"] else 0
    except Exception:
        print("Scan failed or coverage incomplete; do not interpret this as a clean image.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
