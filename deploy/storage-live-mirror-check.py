"""Copy every current MinIO object to isolated RustFS and verify restart survival."""

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
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[1]
IMAGE = "rustfs/rustfs:1.0.0"


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


def start(name, volume, access, secret):
    environment = {
        **os.environ, "RUSTFS_ACCESS_KEY": access, "RUSTFS_SECRET_KEY": secret,
        "RUSTFS_VOLUMES": "/data", "RUSTFS_CONSOLE_ENABLE": "false",
    }
    docker(
        "run", "--rm", "-d", "--name", name, "-p", "127.0.0.1::9000",
        "--mount", f"type=volume,src={volume},dst=/data", "-e", "RUSTFS_ACCESS_KEY",
        "-e", "RUSTFS_SECRET_KEY", "-e", "RUSTFS_VOLUMES", "-e", "RUSTFS_CONSOLE_ENABLE",
        IMAGE, env=environment,
    )
    deadline = time.monotonic() + 45
    last_error = None
    while time.monotonic() < deadline:
        info = json.loads(docker("inspect", name))[0]
        ports = info["NetworkSettings"]["Ports"].get("9000/tcp") or []
        if ports:
            endpoint = "http://127.0.0.1:" + ports[0]["HostPort"]
            try:
                s3(endpoint, access, secret).list_buckets()
                return endpoint
            except Exception as exc:  # noqa: BLE001 - readiness retries startup failures
                last_error = exc
        time.sleep(1)
    raise AssertionError("RustFS candidate did not become ready") from last_error


def objects(store, bucket):
    return {
        item["Key"]: item
        for page in store.get_paginator("list_objects_v2").paginate(Bucket=bucket)
        for item in page.get("Contents", [])
    }


def digest(store, bucket, key):
    body = store.get_object(Bucket=bucket, Key=key)["Body"]
    try:
        return hashlib.sha256(body.read()).hexdigest()
    finally:
        body.close()


def main():
    raw = next(
        line.split("=", 1)[1] for line in (ROOT / "deploy/backend.env").read_text(encoding="utf-8").splitlines()
        if line.startswith("STORAGE=")
    )
    config = json.loads(raw)
    bucket, access, secret = config["bucket"], config["access_key"], config["secret_key"]
    source = s3("http://127.0.0.1:19000", access, secret)
    before = objects(source, bucket)
    assert before, "Current store is empty"
    suffix = uuid.uuid4().hex[:12]
    name = "campus-rustfs-mirror-" + suffix
    retained = os.environ.get("RUSTFS_TARGET_VOLUME")
    if retained and not retained.startswith("ai-campus-office_rustfs_objects_"):
        raise ValueError("Retained candidate volume name must use the project prefix")
    volume = retained or "campus_rustfs_mirror_" + suffix
    existing = os.environ.get("RUSTFS_USE_EXISTING_VOLUME") == "1"
    exists = subprocess.run(
        ["docker", "volume", "inspect", volume], capture_output=True, check=False
    ).returncode == 0
    if retained and exists and not existing:
        raise RuntimeError("Target volume already exists; refusing to overwrite it")
    if existing and not exists:
        raise RuntimeError("Target volume does not exist")
    if not exists:
        docker("volume", "create", volume)
    running = False
    completed = False
    try:
        endpoint = start(name, volume, access, secret)
        running = True
        target = s3(endpoint, access, secret)
        try:
            target.head_bucket(Bucket=bucket)
        except ClientError:
            target.create_bucket(Bucket=bucket)
        expected = {}
        for key in before:
            result = source.get_object(Bucket=bucket, Key=key)
            body = result["Body"]
            try:
                content = body.read()
            finally:
                body.close()
            expected[key] = hashlib.sha256(content).hexdigest()
            options = {
                "Bucket": bucket, "Key": key, "Body": content,
                "ContentType": result.get("ContentType") or "application/octet-stream",
                "Metadata": result.get("Metadata") or {},
            }
            if result.get("ContentDisposition"):
                options["ContentDisposition"] = result["ContentDisposition"]
            target.put_object(**options)

        for stale in set(objects(target, bucket)) - set(before):
            target.delete_object(Bucket=bucket, Key=stale)

        assert set(objects(target, bucket)) == set(before)
        for key, expected_hash in expected.items():
            assert digest(target, bucket, key) == expected_hash, key
            original = source.head_object(Bucket=bucket, Key=key)
            copied = target.head_object(Bucket=bucket, Key=key)
            assert copied["ContentLength"] == original["ContentLength"], key
            assert copied["ContentType"] == original["ContentType"], key
            assert copied.get("Metadata", {}) == original.get("Metadata", {}), key
        assert set(objects(source, bucket)) == set(before), "Source keys changed during mirror test"
        assert all(digest(source, bucket, key) == value for key, value in expected.items())

        docker("stop", name)
        running = False
        endpoint = start(name, volume, access, secret)
        running = True
        target = s3(endpoint, access, secret)
        assert set(objects(target, bucket)) == set(before)
        assert all(digest(target, bucket, key) == value for key, value in expected.items())
        key = next(iter(before))
        signed = target.generate_presigned_url(
            "get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=60
        )
        assert hashlib.sha256(httpx.get(signed, trust_env=False, timeout=30).content).hexdigest() == expected[key]
        assert httpx.get(
            endpoint + "/" + bucket + "/" + key, trust_env=False, timeout=10
        ).status_code == 403
        print(
            f"PASS: mirrored {len(before)} current objects ({sum(x['Size'] for x in before.values())} bytes) "
            "to isolated RustFS 1.0.0, metadata and SHA-256 verified after restart, private signed access"
        )
        completed = True
    finally:
        if running:
            subprocess.run(["docker", "stop", name], capture_output=True, check=False)
        if not retained or (not exists and not completed):
            subprocess.run(["docker", "volume", "rm", volume], capture_output=True, check=False)


if __name__ == "__main__":
    main()
