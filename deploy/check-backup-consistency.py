"""Check a restored PostgreSQL database against an object backup manifest.

Run against a disposable restore container, never the live database. The check is
read-only and exits nonzero when an active object reference is absent or corrupt.
"""

import argparse
import csv
import hashlib
import io
import json
import subprocess
from pathlib import Path

SQL = (
    "COPY (SELECT object_key, sha256 FROM documents WHERE status <> 'deleted' "
    "UNION ALL SELECT object_key, sha256 FROM resource_outputs "
    "WHERE ready AND deleted_at IS NULL) TO STDOUT WITH CSV"
)


def inspect(container: str, backup: Path) -> dict:
    manifest_path = backup / "manifest.json"
    rows = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise TypeError("Object manifest must be a list")
    objects = {}
    corrupt = 0
    for item in rows:
        key, name, digest = item["key"], item["file"], item["sha256"]
        if key in objects or Path(name).name != name:
            raise ValueError("Duplicate key or unsafe manifest filename")
        path = backup / name
        if not path.is_file() or path.stat().st_size != item["bytes"] or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            corrupt += 1
        objects[key] = digest

    result = subprocess.run(
        ["docker", "exec", container, "psql", "-X", "-v", "ON_ERROR_STOP=1",
         "-U", "xflj", "-d", "xflj", "-c", SQL],
        check=True, capture_output=True, text=True,
    )
    refs = list(csv.reader(io.StringIO(result.stdout)))
    missing = sum(key not in objects for key, _ in refs)
    mismatch = sum(key in objects and objects[key] != digest for key, digest in refs)
    referenced = {key for key, _ in refs}
    return {
        "status": "passed" if not (missing or mismatch or corrupt) else "failed",
        "activeReferences": len(refs),
        "backupObjects": len(objects),
        "missingReferences": missing,
        "checksumMismatches": mismatch,
        "corruptBackupObjects": corrupt,
        "unreferencedBackupObjects": sum(key not in referenced for key in objects),
        "pointInTimeVerified": False,
        "scope": "Active object reference integrity only; common snapshot time not established",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-container", required=True, help="Disposable restored PostgreSQL container")
    parser.add_argument("--object-backup", required=True, type=Path)
    args = parser.parse_args()
    report = inspect(args.db_container, args.object_backup)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
