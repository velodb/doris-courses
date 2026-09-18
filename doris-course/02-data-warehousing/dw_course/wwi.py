"""WWI file contracts; no database writes or network access during import."""

import hashlib
import json
import os
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
