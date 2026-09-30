"""Quiesce local writers, capture DB and objects, then restore both in disposable services."""

import hashlib
import json
import os
import secrets
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import quote

import boto3
import httpx
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.config import Settings

COMPOSE = ("docker", "compose", "-f", "compose.app.yaml")
WRITERS = ("frontend", "backend", "worker", "scheduler")
MAX_BACKUP_BYTES = 512 * 1024 * 1024


def run(*args, env=None):
    return subprocess.run(
        args, cwd=ROOT, env=env, check=True, capture_output=True, text=True
    ).stdout.strip()


def s3_client(endpoint, access, secret):
    return boto3.client(
        "s3", endpoint_url=endpoint, region_name="us-east-1",
        aws_access_key_id=access, aws_secret_access_key=secret,
        config=Config(signature_version="s3v4", connect_timeout=3, read_timeout=15,
                      retries={"max_attempts": 1}, s3={"addressing_style": "path"}),
    )


def wait_postgres(container):
    for _ in range(40):
        ready = subprocess.run(
            ["docker", "exec", container, "pg_isready", "-h", "127.0.0.1", "-U", "xflj", "-d", "xflj"],
            capture_output=True, check=False,
        )
        if ready.returncode == 0:
            return
        time.sleep(1)
    raise RuntimeError("Restored PostgreSQL did not become ready")


def wait_minio(container, access, secret):
    for _ in range(40):
        info = json.loads(run("docker", "inspect", container))[0]
        bindings = info["NetworkSettings"]["Ports"].get("9000/tcp") or []
        if bindings:
            endpoint = "http://127.0.0.1:" + bindings[0]["HostPort"]
            client = s3_client(endpoint, access, secret)
            try:
                client.list_buckets()
                return endpoint, client
            except (BotoCoreError, ClientError):
                pass
        time.sleep(1)
    raise RuntimeError("Restored MinIO did not become ready")


def main():
    identity = uuid.uuid4().hex[:12]
    folder = ROOT / "local-data" / ("coordinated-backup-" + identity)
    folder.mkdir()
    db_file = folder / "database.dump"
    remote = "/tmp/coordinated-" + identity + ".dump"
    restore_db = "campus-coordinated-db-" + identity
    restore_store = "campus-coordinated-store-" + identity
    source_db = run(*COMPOSE, "ps", "-q", "postgres")
    if not source_db:
        raise RuntimeError("Live PostgreSQL container not found")
    active = run(
        "docker", "exec", source_db, "psql", "-U", "xflj", "-d", "xflj", "-At", "-c",
        "SELECT (SELECT count(*) FROM resource_tasks WHERE status IN ('queued','running')) + "
        "(SELECT count(*) FROM model_invocations WHERE status='running')",
    )
    if int(active) != 0:
        raise RuntimeError("Active model or resource work prevents a quiesced snapshot")
    settings = Settings(_env_file=ROOT / "deploy/backend.env")
    profile = settings.storage
    source_store = s3_client(
        profile.public_endpoint_url,
        profile.access_key.get_secret_value(),
        profile.secret_key.get_secret_value(),
    )
    quiesced = False
    db_created = False
    store_created = False
    try:
        quiesced = True
        run(*COMPOSE, "stop", *WRITERS)
        stopped = run(*COMPOSE, "ps", "--status", "running", "--services").splitlines()
        if any(service in stopped for service in WRITERS):
            raise RuntimeError("Writer still running after Compose stop")
        snapshot_started = time.time()
        run("docker", "exec", source_db, "pg_dump", "-U", "xflj", "-d", "xflj", "-Fc", "-f", remote)
        run("docker", "cp", source_db + ":" + remote, str(db_file))
        run("docker", "exec", source_db, "rm", "-f", remote)
        manifest = []
        total = 0
        for page in source_store.get_paginator("list_objects_v2").paginate(Bucket=profile.bucket):
            for row in page.get("Contents", []):
                total += row["Size"]
                if total > MAX_BACKUP_BYTES:
                    raise RuntimeError("Bounded audit backup exceeds 512 MiB")
                name = f"{len(manifest):06}.bin"
                source_store.download_file(profile.bucket, row["Key"], str(folder / name))
                content = (folder / name).read_bytes()
                manifest.append({"key": row["Key"], "file": name, "bytes": len(content),
                                 "sha256": hashlib.sha256(content).hexdigest()})
        (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        snapshot_finished = time.time()
    finally:
        if quiesced:
            run(*COMPOSE, "up", "-d", "--no-deps", "--no-build", "backend", "worker", "scheduler", "frontend")
    # Restore only after the live application has resumed.
    db_password = secrets.token_urlsafe(32)
    store_password = secrets.token_urlsafe(32)
    try:
        run("docker", "run", "--rm", "-d", "--name", restore_db, "--network", "none",
            "-e", "POSTGRES_PASSWORD", "-e", "POSTGRES_USER=xflj", "-e", "POSTGRES_DB=xflj",
            "pgvector/pgvector:pg17", env={**os.environ, "POSTGRES_PASSWORD": db_password})
        db_created = True
        wait_postgres(restore_db)
        run("docker", "cp", str(db_file), restore_db + ":/tmp/restore.dump")
        run("docker", "exec", restore_db, "pg_restore", "--exit-on-error", "--no-owner",
            "-U", "xflj", "-d", "xflj", "/tmp/restore.dump")
        check = json.loads(run(
            sys.executable, str(ROOT / "deploy/check-backup-consistency.py"),
            "--db-container", restore_db, "--object-backup", str(folder),
        ))
        if check["status"] != "passed":
            raise RuntimeError("Restored DB has missing or corrupt object references")
        run("docker", "run", "--rm", "-d", "--name", restore_store,
            "-p", "127.0.0.1::9000", "-e", "MINIO_ROOT_USER", "-e", "MINIO_ROOT_PASSWORD",
            "quay.io/minio/minio:RELEASE.2025-09-07T16-13-09Z", "server", "/data",
            env={**os.environ, "MINIO_ROOT_USER": "restore-audit", "MINIO_ROOT_PASSWORD": store_password})
        store_created = True
        endpoint, restored_store = wait_minio(restore_store, "restore-audit", store_password)
        restored_store.create_bucket(Bucket=profile.bucket)
        for item in manifest:
            restored_store.upload_file(str(folder / item["file"]), profile.bucket, item["key"])
            body = restored_store.get_object(Bucket=profile.bucket, Key=item["key"])["Body"]
            try:
                if hashlib.sha256(body.read()).hexdigest() != item["sha256"]:
                    raise RuntimeError("Restored object checksum mismatch")
            finally:
                body.close()
        anonymous_status = None
        if manifest:
            url = endpoint + "/" + profile.bucket + "/" + quote(manifest[0]["key"], safe="/")
            anonymous_status = httpx.get(url, trust_env=False, timeout=10).status_code
            if anonymous_status != 403:
                raise RuntimeError("Restored object is publicly accessible")
        report = {
            "status": "passed", "snapshotQuiesced": True,
            "snapshotDurationSeconds": round(snapshot_finished - snapshot_started, 2),
            "databaseBytes": db_file.stat().st_size, "objects": len(manifest), "objectBytes": total,
            **check, "pointInTimeVerified": True,
            "scope": "Local application writers stopped throughout DB/object capture; disposable DB and MinIO restore verified",
            "restoredObjectChecksumsMatch": True, "anonymousObjectStatus": anonymous_status,
            "liveDataOverwritten": False,
        }
        (ROOT / "docs/COORDINATED-BACKUP-RESTORE.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(report, ensure_ascii=False))
    finally:
        if db_created:
            subprocess.run(["docker", "stop", restore_db], capture_output=True, check=False)
        if store_created:
            subprocess.run(["docker", "stop", restore_store], capture_output=True, check=False)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:  # noqa: BLE001 - never print backend or storage credentials
        print("Coordinated backup validation failed: " + type(error).__name__)
        raise SystemExit(1) from None
