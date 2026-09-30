"""Read-only production dump restored into a disposable, isolated database."""

import json
import os
import secrets
import subprocess
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(*args, **kwargs):
    return subprocess.run(args, cwd=ROOT, check=True, capture_output=True, text=True, **kwargs).stdout.strip()


def main():
    identity = uuid.uuid4().hex[:12]
    container = "campus-restore-check-" + identity
    remote = "/tmp/campus-audit-" + identity + ".dump"
    target = ROOT / "local-data" / ("database-backup-" + identity + ".dump")
    source = run("docker", "compose", "-f", "compose.app.yaml", "ps", "-q", "postgres")
    assert source
    created = False
    try:
        run("docker", "exec", source, "pg_dump", "-U", "xflj", "-d", "xflj", "-Fc", "-f", remote)
        run("docker", "cp", source + ":" + remote, str(target))
        run("docker", "run", "--rm", "-d", "--name", container, "--network", "none",
            "-e", "POSTGRES_PASSWORD", "-e", "POSTGRES_USER=xflj", "-e", "POSTGRES_DB=xflj",
            "pgvector/pgvector:pg17", env={**os.environ, "POSTGRES_PASSWORD": secrets.token_urlsafe(32)})
        created = True
        for _ in range(40):
            # Bootstrap's temporary server accepts Unix sockets only; wait for the final TCP server.
            ready = subprocess.run(["docker", "exec", container, "pg_isready", "-h", "127.0.0.1", "-U", "xflj", "-d", "xflj"],
                                   capture_output=True)
            if ready.returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError("Restore database unavailable")
        run("docker", "cp", str(target), container + ":/tmp/restore.dump")
        run("docker", "exec", container, "pg_restore", "--exit-on-error", "--no-owner",
            "-U", "xflj", "-d", "xflj", "/tmp/restore.dump")
        sql = "SELECT version_num FROM alembic_version; SELECT count(*) FROM users; SELECT count(*) FROM quota_transactions;"
        restored = run("docker", "exec", container, "psql", "-U", "xflj", "-d", "xflj", "-At", "-c", sql)
        current = run("docker", "exec", source, "psql", "-U", "xflj", "-d", "xflj", "-At", "-c", sql)
        assert restored == current, "Source counts changed or restored database differs"
        report = {"status": "passed", "backupBytes": target.stat().st_size,
                  "migration": restored.splitlines()[0], "users": int(restored.splitlines()[1]),
                  "ledgerRows": int(restored.splitlines()[2]), "isolatedNetwork": True,
                  "scope": "PostgreSQL logical backup and restore; object files and complete disaster recovery not covered"}
        (ROOT / "docs/BACKUP-RESTORE-TEST.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report))
    finally:
        # Only exact temporary files and the uniquely named disposable container are removed.
        subprocess.run(["docker", "exec", source, "rm", "-f", remote], capture_output=True)
        if created:
            subprocess.run(["docker", "stop", container], capture_output=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("Backup restore validation failed: " + type(error).__name__)
        if isinstance(error, subprocess.CalledProcessError):
            print("Failed operation: " + " ".join(error.cmd[:7]))
            print((error.stderr or "").splitlines()[0][:250])
        raise SystemExit(1) from None
