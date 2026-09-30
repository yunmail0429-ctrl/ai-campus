"""Test an isolated S3 candidate against the app's actual stored objects."""

import argparse
import hashlib
import json
import os
import secrets
import subprocess
import time
import uuid
from pathlib import Path
from urllib.parse import quote

import boto3
import httpx
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

ROOT = Path(__file__).resolve().parents[1]


def docker(*args, env=None):
    return subprocess.run(
        ["docker", *args], cwd=ROOT, env=env, capture_output=True, text=True, check=True
    ).stdout.strip()


def client(endpoint, access, secret):
    return boto3.client(
        "s3", endpoint_url=endpoint, region_name="us-east-1",
        aws_access_key_id=access, aws_secret_access_key=secret,
        config=Config(signature_version="s3v4", connect_timeout=3, read_timeout=15,
                      retries={"max_attempts": 1}, s3={"addressing_style": "path"}),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="rustfs/rustfs:1.0.0-rc.6")
    parser.add_argument("--backup", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads((args.backup / "manifest.json").read_text(encoding="utf-8"))
    assert manifest
    container = "campus-storage-candidate-" + uuid.uuid4().hex[:12]
    access, secret = "candidate-audit", secrets.token_urlsafe(32)
    environment = {**os.environ, "RUSTFS_ACCESS_KEY": access, "RUSTFS_SECRET_KEY": secret,
                   "RUSTFS_VOLUMES": "/data"}
    started = False
    try:
        docker("run", "--rm", "-d", "--name", container, "-p", "127.0.0.1::9000",
               "--mount", "type=volume,target=/data", "-e", "RUSTFS_ACCESS_KEY",
               "-e", "RUSTFS_SECRET_KEY", "-e", "RUSTFS_VOLUMES", args.image, env=environment)
        started = True
        for _ in range(40):
            info = json.loads(docker("inspect", container))[0]
            ports = info["NetworkSettings"]["Ports"].get("9000/tcp") or []
            if ports:
                endpoint = "http://127.0.0.1:" + ports[0]["HostPort"]
                store = client(endpoint, access, secret)
                try:
                    store.list_buckets()
                    break
                except (BotoCoreError, ClientError):
                    pass
            time.sleep(1)
        else:
            raise RuntimeError("Candidate S3 endpoint did not become ready")
        bucket = "school-candidate-audit"
        store.create_bucket(Bucket=bucket)
        assert store.head_bucket(Bucket=bucket)["ResponseMetadata"]["HTTPStatusCode"] == 200
        acl = store.get_bucket_acl(Bucket=bucket)
        assert not any(
            grant.get("Grantee", {}).get("Type") == "Group" for grant in acl.get("Grants", [])
        )
        try:
            policy = json.loads(store.get_bucket_policy(Bucket=bucket)["Policy"])
            statements = policy.get("Statement", [])
            if isinstance(statements, dict):
                statements = [statements]
            assert not any(
                row.get("Effect") == "Allow" and "*" in json.dumps(row.get("Principal", {}))
                for row in statements
            )
        except ClientError as exc:
            assert exc.response.get("Error", {}).get("Code") in ("NoSuchBucketPolicy", "NoSuchPolicy")
        for item in manifest:
            path = args.backup / item["file"]
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            assert digest == item["sha256"]
            store.upload_file(str(path), bucket, item["key"])
            body = store.get_object(Bucket=bucket, Key=item["key"])["Body"]
            try:
                assert hashlib.sha256(body.read()).hexdigest() == digest
            finally:
                body.close()
        first = manifest[0]
        url = store.generate_presigned_url(
            "get_object", Params={
                "Bucket": bucket, "Key": first["key"],
                "ResponseContentType": "application/octet-stream",
                "ResponseContentDisposition": "attachment; filename=download; filename*=UTF-8''test.pptx",
            }, ExpiresIn=60
        )
        signed = httpx.get(url, trust_env=False, timeout=15)
        assert signed.status_code == 200
        assert hashlib.sha256(signed.content).hexdigest() == first["sha256"]
        assert signed.headers["content-type"] == "application/octet-stream"
        assert "attachment" in signed.headers["content-disposition"]
        assert store.head_object(Bucket=bucket, Key=first["key"])["ContentLength"] > 0
        disposable = "audit/disposable-" + uuid.uuid4().hex
        store.put_object(Bucket=bucket, Key=disposable, Body=b"delete verification",
                         ContentType="application/octet-stream")
        store.delete_object(Bucket=bucket, Key=disposable)
        try:
            store.get_object(Bucket=bucket, Key=disposable)
        except ClientError as exc:
            assert exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404")
        else:
            raise AssertionError("Deleted object is still readable")
        public_url = endpoint + "/" + bucket + "/" + quote(first["key"], safe="/")
        anonymous = httpx.get(public_url, trust_env=False, timeout=15)
        assert anonymous.status_code == 403
        report = {"image": args.image, "status": "passed", "objects": len(manifest),
                  "uploadAndReadChecksumsMatch": True, "signedDownload": signed.status_code,
                  "anonymousDownload": anonymous.status_code, "bucketAclAndPolicyPrivate": True,
                  "deleteRemovesObject": True, "liveStorageChanged": False,
                  "scope": "Isolated S3 API compatibility with current backup, not production migration"}
        (ROOT / "docs/STORAGE-CANDIDATE-TEST.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(report))
    finally:
        if started:
            subprocess.run(["docker", "stop", container], capture_output=True, check=False)


if __name__ == "__main__":
    main()
