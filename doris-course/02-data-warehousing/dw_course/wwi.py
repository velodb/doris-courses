"""WWI file contracts; no database writes or network access during import."""

import hashlib
import json
import os
import tarfile
import tempfile
from pathlib import Path

from .runtime import COURSE_ROOT, identifier

HISTORY_COLUMNS = (
    "order_id", "customer_id", "order_date", "order_amount", "line_count", "data_source",
)


def sample():
    return json.loads((COURSE_ROOT / "datasets/wwi/sample.json").read_text())


def manifest():
    return json.loads((COURSE_ROOT / "datasets/wwi/manifest.json").read_text())


def history_rows():
    return [tuple(row[column] for column in HISTORY_COLUMNS) for row in sample()["orders"]]


def history_ddl(table):
    return f"""CREATE TABLE {identifier(table)} (
    order_id BIGINT NOT NULL,
    customer_id BIGINT NOT NULL,
    order_date DATE NOT NULL,
    order_amount DECIMAL(18,2) NOT NULL,
    line_count INT NOT NULL,
    data_source VARCHAR(32) NOT NULL
) DUPLICATE KEY(order_id)
DISTRIBUTED BY HASH(order_id) BUCKETS 1
PROPERTIES ("replication_num"="1")"""


def parquet_ddl(name, table):
    source = manifest()["tables"][name]
    prefix = f"CREATE TABLE `{name}`"
    if not source["ddl"].startswith(prefix):
        raise ValueError("Manifest DDL does not match the source table")
    return f"CREATE TABLE `{identifier(table)}`" + source["ddl"][len(prefix):]


def parquet_paths(directory=None):
    """Validate all files before a lab resets any of its target tables."""
    root = Path(directory or os.environ.get("DW_WWI_DATA_DIR", COURSE_ROOT / ".runtime/wwi"))
    if directory is None and not os.environ.get("DW_WWI_DATA_DIR") and not root.exists():
        _unpack_bundle(root)
    paths = {}
    for name, entry in manifest()["tables"].items():
        path = root / f"{name}.parquet"
        if not path.is_file():
            raise FileNotFoundError(f"缺少 {path}；请按 datasets/README.md 准备本地 WWI 数据包。")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        if path.stat().st_size != entry["bytes"] or digest.hexdigest() != entry["sha256"]:
            raise ValueError(f"WWI 数据文件与课程 manifest 不一致：{path}")
        paths[name] = path
    return paths


def _unpack_bundle(root):
    """Publish a validated local bundle; never replace existing learner files."""
    root.parent.mkdir(parents=True, exist_ok=True)
    entries = {name + ".parquet": entry for name, entry in manifest()["tables"].items()}
    with tempfile.TemporaryDirectory(prefix="wwi-", dir=root.parent) as temporary:
        unpacked = Path(temporary) / "data"
        unpacked.mkdir()
        with tarfile.open(COURSE_ROOT / "datasets/wwi/wwi-core.tar.gz", "r:gz") as archive:
            members = archive.getmembers()
            if len(members) != len(entries) or {m.name for m in members} != set(entries):
                raise ValueError("WWI 数据包文件清单与 manifest 不一致")
            for member in members:
                if not member.isfile() or member.size != entries[member.name]["bytes"]:
                    raise ValueError("WWI 数据包包含不符合 manifest 的文件")
                with archive.extractfile(member) as source, (unpacked / member.name).open("xb") as target:
                    for block in iter(lambda: source.read(1024 * 1024), b""):
                        target.write(block)
        parquet_paths(unpacked)
        # A concurrent preparation must not replace a directory published by another kernel.
        if root.exists():
            parquet_paths(root)
        else:
            unpacked.rename(root)
