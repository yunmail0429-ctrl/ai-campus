"""Restore the coordinated backup into an isolated PostgreSQL candidate image."""

import argparse
import json
import os
import secrets
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def docker(*args, env=None):
    return subprocess.run(
        ["docker", *args], cwd=ROOT, env=env, capture_output=True, text=True, check=True
    ).stdout.strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--image", default="campus-postgres-candidate:pg17-vector086")
    args = parser.parse_args()
    name = "campus-pg-candidate-" + uuid.uuid4().hex[:12]
    password = secrets.token_urlsafe(32)
    started = False
    try:
        docker("run", "--rm", "-d", "--name", name, "--network", "none",
               "-e", "POSTGRES_PASSWORD", "-e", "POSTGRES_USER=xflj", "-e", "POSTGRES_DB=xflj",
               args.image, env={**os.environ, "POSTGRES_PASSWORD": password})
        started = True
        for _ in range(40):
            ready = subprocess.run(
                ["docker", "exec", name, "pg_isready", "-U", "xflj", "-d", "xflj"],
                capture_output=True, check=False,
            )
            if ready.returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError("Candidate PostgreSQL did not become ready")
        docker("cp", str(args.backup / "database.dump"), name + ":/tmp/restore.dump")
        docker("exec", name, "pg_restore", "--exit-on-error", "--no-owner",
               "-U", "xflj", "-d", "xflj", "/tmp/restore.dump")
        result = docker("exec", name, "psql", "-U", "xflj", "-d", "xflj", "-Atc",
                        "select json_build_object('collation',datcollate,'ctype',datctype,"
                        "'vectorVersion',(select extversion from pg_extension where extname='vector'),"
                        "'users',(select count(*) from users),"
                        "'modelInvocations',(select count(*) from model_invocations),"
                        "'resourceTasks',(select count(*) from resource_tasks)) "
                        "from pg_database where datname=current_database()")
        facts = json.loads(result)
        assert facts["vectorVersion"] == "0.8.6"
        consistency = json.loads(subprocess.run(
            [sys.executable, str(ROOT / "deploy/check-backup-consistency.py"),
             "--db-container", name, "--object-backup", str(args.backup)],
            cwd=ROOT, capture_output=True, text=True, check=True,
        ).stdout)
        assert consistency["status"] == "passed"
        report = {"status": "passed", "image": args.image, "isolatedRestore": True,
                  "liveDatabaseChanged": False, "objectReferences": consistency, **facts,
                  "scope": "Backup restore and basic counts only; not collation or full application regression"}
        (ROOT / "docs/POSTGRES-CANDIDATE-TEST.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(report))
    finally:
        if started:
            subprocess.run(["docker", "stop", name], capture_output=True, check=False)


if __name__ == "__main__":
    main()
