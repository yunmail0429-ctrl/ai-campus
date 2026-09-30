"""Back up actual objects and verify restoration to a disposable MinIO server."""

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
from app.config import Settings  # noqa: E402


def docker(*args):
    return subprocess.check_output(["docker", *args], text=True).strip()


def client(endpoint, access, secret):
    return boto3.client("s3", endpoint_url=endpoint, region_name="us-east-1",
                        aws_access_key_id=access, aws_secret_access_key=secret,
                        config=Config(signature_version="s3v4", connect_timeout=3, read_timeout=15,
                                      retries={"max_attempts": 1}, s3={"addressing_style": "path"}))


def main():
    settings = Settings(_env_file=ROOT / "deploy/backend.env")
    profile = settings.storage
    source = client(profile.public_endpoint_url, profile.access_key.get_secret_value(), profile.secret_key.get_secret_value())
    identity = uuid.uuid4().hex[:12]
    folder = ROOT / "local-data" / ("object-backup-" + identity)
    folder.mkdir()
    manifest, total = [], 0
    for page in source.get_paginator("list_objects_v2").paginate(Bucket=profile.bucket):
        for row in page.get("Contents", []):
            total += row["Size"]
            if total > 512 * 1024 * 1024:
                raise RuntimeError("Backup exceeds this bounded audit's 512 MiB limit")
            name = f"{len(manifest):06}.bin"
            source.download_file(profile.bucket, row["Key"], str(folder / name))
            content = (folder / name).read_bytes()
            manifest.append({"key": row["Key"], "file": name, "bytes": len(content),
                             "sha256": hashlib.sha256(content).hexdigest()})
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert manifest, "No stored files: cannot validate object restoration"
    container = "campus-object-restore-" + identity
    secret = secrets.token_urlsafe(32)
    subprocess.run(["docker", "run", "--rm", "-d", "--name", container, "-p", "127.0.0.1::9000",
                    "-e", "MINIO_ROOT_USER", "-e", "MINIO_ROOT_PASSWORD",
                    "quay.io/minio/minio:RELEASE.2025-09-07T16-13-09Z", "server", "/data"],
                   env={**os.environ, "MINIO_ROOT_USER": "restore-audit", "MINIO_ROOT_PASSWORD": secret},
                   check=True, stdout=subprocess.DEVNULL)
    try:
        for _ in range(40):
            info = json.loads(docker("inspect", container))[0]
            bindings = info["NetworkSettings"]["Ports"].get("9000/tcp") or []
            if bindings:
                endpoint = "http://127.0.0.1:" + bindings[0]["HostPort"]
                restored = client(endpoint, "restore-audit", secret)
                try:
                    restored.list_buckets()
                    break
                except (BotoCoreError, ClientError):
                    pass
            time.sleep(1)
        else:
            raise RuntimeError("Restore storage did not become ready")
        restored.create_bucket(Bucket=profile.bucket)
        for item in manifest:
            restored.upload_file(str(folder / item["file"]), profile.bucket, item["key"])
            body = restored.get_object(Bucket=profile.bucket, Key=item["key"])["Body"]
            try:
                assert hashlib.sha256(body.read()).hexdigest() == item["sha256"]
            finally:
                body.close()
        response = httpx.get(endpoint + "/" + profile.bucket + "/" + quote(manifest[0]["key"], safe="/"), trust_env=False)
        assert response.status_code == 403
        report = {"status": "passed", "objects": len(manifest), "bytes": total,
                  "allChecksumsMatch": True, "anonymousDownload": response.status_code,
                  "scope": "Current object contents, separate disposable MinIO; database/object point-in-time consistency not tested"}
        (ROOT / "docs/OBJECT-RESTORE-TEST.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report))
    finally:
        subprocess.run(["docker", "stop", container], stdout=subprocess.DEVNULL, check=False)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("Object restore validation failed: " + type(error).__name__)
        raise SystemExit(1) from None
