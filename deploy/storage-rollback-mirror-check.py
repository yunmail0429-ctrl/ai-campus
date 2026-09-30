"""Synchronize current RustFS objects into the retained legacy MinIO volume."""

import hashlib
import json
import os
import subprocess
import time
import uuid
from pathlib import Path

import boto3
import httpx
from botocore.config import Config

ROOT = Path(__file__).resolve().parents[1]
LEGACY_VOLUME = "ai-campus-office_objects"
LEGACY_IMAGE = "quay.io/minio/minio:RELEASE.2025-09-07T16-13-09Z"


def docker(*args, env=None):
    return subprocess.run(
        ["docker", *args], cwd=ROOT, env=env, capture_output=True, text=True, check=True
    ).stdout.strip()


def s3(endpoint, access, secret):
    return boto3.client(
        "s3", endpoint_url=endpoint, region_name="us-east-1",
        aws_access_key_id=access, aws_secret_access_key=secret,
        config=Config(signature_version="s3v4", connect_timeout=3, read_timeout=30,
                      retries={"max_attempts": 2}, s3={"addressing_style": "path"}),
    )


def objects(store, bucket):
    return {
        row["Key"]: row
        for page in store.get_paginator("list_objects_v2").paginate(Bucket=bucket)
        for row in page.get("Contents", [])
    }


def digest(store, bucket, key):
    body = store.get_object(Bucket=bucket, Key=key)["Body"]
    try:
        return hashlib.sha256(body.read()).hexdigest()
    finally:
        body.close()


def main():
    if docker("volume", "inspect", LEGACY_VOLUME) == "":
        raise AssertionError("Legacy volume was not found")
    raw = next(
        row.split("=", 1)[1] for row in (ROOT / "deploy/backend.env").read_text(encoding="utf-8").splitlines()
        if row.startswith("STORAGE=")
    )
    config = json.loads(raw)
    bucket, access, secret = config["bucket"], config["access_key"], config["secret_key"]
    current = s3("http://127.0.0.1:19000", access, secret)
    before = objects(current, bucket)
    assert before
    name = "campus-legacy-rollback-" + uuid.uuid4().hex[:12]
    environment = {**os.environ, "MINIO_ROOT_USER": access, "MINIO_ROOT_PASSWORD": secret}
    started = False
    try:
        docker(
            "run", "--rm", "-d", "--name", name, "-p", "127.0.0.1::9000",
            "--mount", f"type=volume,src={LEGACY_VOLUME},dst=/data",
            "-e", "MINIO_ROOT_USER", "-e", "MINIO_ROOT_PASSWORD", LEGACY_IMAGE,
            "server", "/data", env=environment,
        )
        started = True
        deadline = time.monotonic() + 45
        last_error = None
        while time.monotonic() < deadline:
            info = json.loads(docker("inspect", name))[0]
            ports = info["NetworkSettings"]["Ports"].get("9000/tcp") or []
            if ports:
                endpoint = "http://127.0.0.1:" + ports[0]["HostPort"]
                legacy = s3(endpoint, access, secret)
                try:
                    legacy.list_buckets()
                    break
                except Exception as exc:  # noqa: BLE001 - readiness retries startup failures
                    last_error = exc
            time.sleep(1)
        else:
            raise AssertionError("Legacy MinIO did not become ready") from last_error
        assert legacy.head_bucket(Bucket=bucket)["ResponseMetadata"]["HTTPStatusCode"] == 200
        expected = {}
        for key in before:
            response = current.get_object(Bucket=bucket, Key=key)
            body = response["Body"]
            try:
                content = body.read()
            finally:
                body.close()
            expected[key] = hashlib.sha256(content).hexdigest()
            options = {
                "Bucket": bucket, "Key": key, "Body": content,
                "ContentType": response.get("ContentType") or "application/octet-stream",
                "Metadata": response.get("Metadata") or {},
            }
            if response.get("ContentDisposition"):
                options["ContentDisposition"] = response["ContentDisposition"]
            legacy.put_object(**options)
        for stale in set(objects(legacy, bucket)) - set(before):
            legacy.delete_object(Bucket=bucket, Key=stale)
        assert set(objects(legacy, bucket)) == set(before)
        for key, value in expected.items():
            assert digest(legacy, bucket, key) == value, key
            old = legacy.head_object(Bucket=bucket, Key=key)
            new = current.head_object(Bucket=bucket, Key=key)
            assert old["ContentLength"] == new["ContentLength"], key
            assert old["ContentType"] == new["ContentType"], key
            assert old.get("Metadata", {}) == new.get("Metadata", {}), key
        assert set(objects(current, bucket)) == set(before), "Live objects changed during rollback sync"
        assert all(digest(current, bucket, key) == value for key, value in expected.items())
        key = next(iter(before))
        signed = legacy.generate_presigned_url(
            "get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=60
        )
        assert hashlib.sha256(httpx.get(signed, trust_env=False, timeout=30).content).hexdigest() == expected[key]
        print(f"PASS: legacy rollback volume mirrors {len(before)} current objects with matching SHA-256 and metadata")
    finally:
        if started:
            subprocess.run(["docker", "stop", name], capture_output=True, check=False)


if __name__ == "__main__":
    main()
