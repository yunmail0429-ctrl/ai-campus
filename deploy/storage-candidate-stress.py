"""Validate a stable RustFS candidate under concurrent IO and restart."""

import hashlib
import json
import os
import secrets
import subprocess
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import boto3
import httpx
from botocore.config import Config
from botocore.exceptions import ClientError

IMAGE = "rustfs/rustfs:1.0.0"
BUCKET = "campus-storage-stress"


def docker(*args, env=None):
    return subprocess.run(
        ["docker", *args], env=env, capture_output=True, text=True, check=True
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
            except Exception as exc:  # noqa: BLE001 - readiness probe retries transient start errors
                last_error = exc
        time.sleep(1)
    raise AssertionError("RustFS did not become ready") from last_error


def main():
    suffix = uuid.uuid4().hex[:12]
    name, volume = "campus-rustfs-stress-" + suffix, "campus_rustfs_stress_" + suffix
    access, secret = "candidate-stress", secrets.token_urlsafe(32)
    docker("volume", "create", volume)
    running = False
    try:
        endpoint = start(name, volume, access, secret)
        running = True
        s3(endpoint, access, secret).create_bucket(Bucket=BUCKET)
        payloads = [(f"stress/object-{index}", secrets.token_bytes(5 * 1024 * 1024)) for index in range(8)]

        def put(entry):
            key, content = entry
            s3(endpoint, access, secret).put_object(Bucket=BUCKET, Key=key, Body=content)

        started = time.monotonic()
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(put, payloads))
        write_seconds = time.monotonic() - started
        docker("stop", name)
        running = False
        endpoint = start(name, volume, access, secret)
        running = True

        def verify(entry):
            key, content = entry
            body = s3(endpoint, access, secret).get_object(Bucket=BUCKET, Key=key)["Body"]
            try:
                return hashlib.sha256(body.read()).digest() == hashlib.sha256(content).digest()
            finally:
                body.close()

        started = time.monotonic()
        with ThreadPoolExecutor(max_workers=8) as pool:
            assert all(pool.map(verify, payloads))
        read_seconds = time.monotonic() - started
        store = s3(endpoint, access, secret)
        signed = store.generate_presigned_url(
            "get_object", Params={"Bucket": BUCKET, "Key": payloads[0][0]}, ExpiresIn=60
        )
        assert httpx.get(signed, trust_env=False, timeout=30).content == payloads[0][1]
        assert httpx.get(
            endpoint + "/" + BUCKET + "/" + payloads[0][0], trust_env=False, timeout=10
        ).status_code == 403
        for key, _ in payloads:
            store.delete_object(Bucket=BUCKET, Key=key)
            try:
                store.get_object(Bucket=BUCKET, Key=key)
            except ClientError as exc:
                assert exc.response["Error"]["Code"] in ("NoSuchKey", "404")
            else:
                raise AssertionError("Deleted object remains readable")
        print(
            f"PASS: RustFS 1.0.0, 8 concurrent 5 MiB PUTs in {write_seconds:.2f}s, "
            f"restart, 8 concurrent checksum GETs in {read_seconds:.2f}s, private signed read and deletion"
        )
    finally:
        if running:
            subprocess.run(["docker", "stop", name], capture_output=True, check=False)
        subprocess.run(["docker", "volume", "rm", volume], capture_output=True, check=False)


if __name__ == "__main__":
    main()
