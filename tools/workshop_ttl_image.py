#!/usr/bin/env python3
"""Build a committed TTL runner locally and verify it without cloud access."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
IMAGE_CHECK = "/opt/doris-courses/workshop/aws/ttl/image_check.py"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def run(arguments, **kwargs):
    result = subprocess.run(arguments, capture_output=True, text=True, check=True, **kwargs)
    return result.stdout


def source_images(source_sha):
    require(re.fullmatch(r"[0-9a-f]{40}", source_sha), "use a full committed source SHA")
    images = json.loads(run(["git", "-C", str(ROOT), "show", source_sha + ":workshop/aws/ttl/images.json"]))
    require(images["schema_version"] == 1 and images["platform"] == "linux/amd64", "unsupported image contract")
    for name, repository in (("python", "docker.io/library/python"), ("terraform", "docker.io/hashicorp/terraform")):
        require(re.fullmatch(re.escape(repository) + r"@sha256:[0-9a-f]{64}", images[name]["image"]), "use reviewed publisher image digests")
    return images


def provider_context(source_sha, mirror, directory):
    """Copy only one public ZIP, verified against both committed lock files."""
    locks = [run(["git", "-C", str(ROOT), "show", source_sha + f":workshop/aws/terraform/{module}/.terraform.lock.hcl"])
        for module in ("pilot", "access-bindings")]
    versions = [re.findall(r'version\s*=\s*"([0-9.]+)"', lock) for lock in locks]
    require(len(versions[0]) == 1 and versions[0] == versions[1], "provider versions differ")
    relative = Path("registry.terraform.io/hashicorp/aws") / f"terraform-provider-aws_{versions[0][0]}_linux_amd64.zip"
    archive = Path(mirror) / relative
    require(archive.is_file() and not archive.is_symlink() and 0 < archive.stat().st_size < 512 * 1024 ** 2, "invalid cached provider ZIP")
    with archive.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    require(all('"zh:' + digest + '"' in lock for lock in locks), "provider ZIP differs from the committed checksums")
    destination = Path(directory) / "provider-context/mirror" / relative
    destination.parent.mkdir(parents=True)
    shutil.copyfile(archive, destination)
    return ["--build-context", "provider-fetch=" + str(Path(directory) / "provider-context")], digest


def wheel_context(source_sha, wheelhouse, directory):
    spec = json.loads(run(["git", "-C", str(ROOT), "show", source_sha + ":workshop/aws/ttl/wheels.json"]))
    require(spec["schema_version"] == 1 and spec["platform"] == "linux/amd64" and 0 < len(spec["wheels"]) <= 32, "invalid wheel cache contract")
    destination = Path(directory) / "wheel-context/wheels"
    destination.mkdir(parents=True)
    for row in spec["wheels"]:
        require(re.fullmatch(r"[A-Za-z0-9_.+-]+\.whl", row["name"]), "invalid wheel filename")
        path = Path(wheelhouse) / row["name"]
        require(path.is_file() and not path.is_symlink() and path.stat().st_size == row["size"] <= 32 * 1024 ** 2, "invalid cached wheel")
        with path.open("rb") as stream:
            require(hashlib.file_digest(stream, "sha256").hexdigest() == row["sha256"], "cached wheel differs from committed artifact hash")
        shutil.copyfile(path, destination / row["name"])
    return ["--build-context", "python-fetch=" + str(destination.parent)], len(spec["wheels"])


def build(source_sha, tag, output, mirror=None, wheelhouse=None):
    require(re.fullmatch(r"[0-9a-f]{40}", source_sha) and
        run(["git", "-C", str(ROOT), "cat-file", "-t", source_sha]).strip() == "commit", "select an existing full commit SHA")
    images = source_images(source_sha)
    require(re.fullmatch(r"doris-workshop-ttl:[a-zA-Z0-9_.-]+", tag), "use a distinct local doris-workshop-ttl tag")
    with tempfile.TemporaryDirectory(prefix="ttl-source-") as directory:
        context_args, provider_hash = provider_context(source_sha, mirror, directory) if mirror else ([], None)
        wheel_args, wheel_count = wheel_context(source_sha, wheelhouse, directory) if wheelhouse else ([], None)
        archive = Path(directory) / "source.tar"
        subprocess.run(["git", "-C", str(ROOT), "archive", "--format=tar", "--output=" + str(archive), source_sha], check=True)
        with archive.open("rb") as context, output.with_suffix(".build.txt").open("x") as log:
            subprocess.run(["docker", "buildx", "build", *context_args, *wheel_args, "--platform", images["platform"], "--load", "--progress", "plain",
                "--build-arg", "PYTHON_IMAGE=" + images["python"]["image"], "--build-arg", "TERRAFORM_IMAGE=" + images["terraform"]["image"],
                "--build-arg", "SOURCE_SHA=" + source_sha, "-f", "workshop/aws/ttl/Dockerfile", "-t", tag, "-"],
                stdin=context, stdout=log, stderr=subprocess.STDOUT, check=True)
    item = json.loads(run(["docker", "image", "inspect", tag]))[0]
    return {"source_sha": source_sha, "image_id": item["Id"], "tag": tag, "base_images": images, "cached_provider_zip_sha256": provider_hash,
        "cached_wheel_count": wheel_count, "pushed": False}


def verify(image, source_sha, output):
    require(re.fullmatch(r"[0-9a-f]{40}", source_sha), "use a full committed source SHA")
    require(re.fullmatch(r"sha256:[0-9a-f]{64}|doris-workshop-ttl:[a-zA-Z0-9_.-]+", image), "select a local runner image ID or tag")
    item = json.loads(run(["docker", "image", "inspect", image]))[0]
    config = item["Config"]
    require(item["Os"] == "linux" and item["Architecture"] == "amd64" and config["User"] == "10001:10001", "wrong image platform or user")
    require(config["Labels"]["org.opencontainers.image.revision"] == source_sha, "image revision differs")
    require(config["Entrypoint"] == ["python", "-I", "/opt/doris-courses/workshop/aws/ttl/runner_entry.py"], "unexpected production entry point")
    require(set(config["Volumes"]) == {"/work"}, "unexpected writable image volume")
    base = ["docker", "run", "--rm", "--pull=never", "--platform", "linux/amd64", "--network", "none", "--read-only", "--cap-drop=ALL",
        "--security-opt", "no-new-privileges", "--memory=1g", "--cpus=0.5", "--pids-limit=256"]
    def isolated(arguments, timeout):
        name = "doris-ttl-check-" + uuid.uuid4().hex
        try:
            return subprocess.run([*base, "--name", name, *arguments], capture_output=True, text=True, timeout=timeout)
        finally:
            # Remove only this invocation's container and anonymous volume,
            # including when the CLI times out before --rm can finish.
            subprocess.run(["docker", "rm", "-f", "-v", name], capture_output=True, timeout=30)
    with output.with_suffix(".verify.txt").open("x") as log:
        result = isolated(["--entrypoint", "python", item["Id"], "-I", IMAGE_CHECK, "--source-sha", source_sha], 900)
        log.write(result.stdout + result.stderr)
        require(result.returncode == 0, "isolated image checks failed; inspect the local verification log")
    report = json.loads(result.stdout)
    for env in ([], ["-e", "TTL_ACCOUNT_ID=000000000000", "-e", "TTL_REGION=us-east-1", "-e", "AWS_PROFILE=image-fixture"]):
        rejected = isolated([*env, item["Id"]], 30)
        require(rejected.returncode == 78 and not rejected.stdout and "TTL request failed;" in rejected.stderr, "production entry point did not fail closed")
    return {"source_sha": source_sha, "image_id": item["Id"], "network": "none", "memory_limit_bytes": 1073741824,
        "cpu_limit": 0.5, "production_entry_rejections": 2, "runtime": report, "fargate_accepted": False, "pushed": False}


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("build", "verify"):
        command = commands.add_parser(name)
        command.add_argument("--source-sha", required=True)
        command.add_argument("--image", required=True)
        command.add_argument("--output", type=Path, required=True)
        if name == "build":
            command.add_argument("--provider-mirror", type=Path, help="Optional public provider mirror; both committed lock checksums are required")
            command.add_argument("--wheelhouse", type=Path, help="Optional public wheels; every committed artifact checksum is required")
    args = parser.parse_args()
    try:
        require(not args.output.exists(), "choose a new local result path")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        result = build(args.source_sha, args.image, args.output, args.provider_mirror, args.wheelhouse) if args.command == "build" else verify(args.image, args.source_sha, args.output)
        with args.output.open("x") as stream:
            json.dump(result, stream, sort_keys=True, indent=2)
            stream.write("\n")
        print("Local image operation passed; no image or cloud resource was published.")
        return 0
    except Exception:
        print("Local image operation failed; inspect the private build/verification log.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
