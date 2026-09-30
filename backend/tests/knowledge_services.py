"""Disposable real MinIO, Redis and ClamAV; random ports/credentials, no shared volumes."""

import json
import os
import secrets
import socket
import subprocess
import time
import uuid
from contextlib import contextmanager

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from redis import Redis


@contextmanager
def services():
    names = []
    prefix = "xflj-knowledge-test-" + uuid.uuid4().hex[:8]
    user, password = "xflj-test", secrets.token_hex(24)
    redis_password = secrets.token_hex(24)

    def start(label, image, port, env=None, args=()):
        name = f"{prefix}-{label}"
        command = ["docker", "run", "--rm", "-d", "--name", name, "-p", f"127.0.0.1::{port}"]
        for key in env or {}:
            command += ["-e", key]
        command += [image, *args]
        subprocess.run(command, env={**os.environ, **(env or {})}, check=True, stdout=subprocess.DEVNULL)
        names.append(name)
        # Docker Desktop may report a running container before publishing its
        # random host port. Wait for the binding instead of indexing an empty list.
        for _ in range(30):
            info = json.loads(subprocess.check_output(["docker", "inspect", name], text=True))[0]
            bindings = info.get("NetworkSettings", {}).get("Ports", {}).get(f"{port}/tcp") or []
            if bindings:
                return int(bindings[0]["HostPort"])
            if info.get("State", {}).get("Status") in ("exited", "dead"):
                raise RuntimeError(f"Test service {label} exited before publishing its port")
            time.sleep(1)
        raise RuntimeError(f"Test service {label} did not publish its port within 30 seconds")

    try:
        minio = start(
            "s3",
            "quay.io/minio/minio:RELEASE.2025-09-07T16-13-09Z",
            9000,
            {"MINIO_ROOT_USER": user, "MINIO_ROOT_PASSWORD": password},
            ["server", "/data"],
        )
        # Password is provided via environment, not echoed into the process command line.
        redis_port = start(
            "redis",
            "redis:7-alpine",
            6379,
            {"REDIS_TEST_PASSWORD": redis_password},
            ["sh", "-c", 'exec redis-server --requirepass "$REDIS_TEST_PASSWORD"'],
        )
        clamav = start("scan", "clamav/clamav:stable", 3310)
        storage = {
            "bucket": "xflj-test-private",
            "endpoint_url": f"http://127.0.0.1:{minio}",
            "access_key": user,
            "secret_key": password,
        }
        client = boto3.client(
            "s3",
            endpoint_url=storage["endpoint_url"],
            region_name="us-east-1",
            aws_access_key_id=user,
            aws_secret_access_key=password,
            config=Config(connect_timeout=1, read_timeout=1, retries={"max_attempts": 0}),
        )
        redis_url = f"redis://:{redis_password}@127.0.0.1:{redis_port}/0"
        cache = Redis.from_url(redis_url, socket_connect_timeout=1, socket_timeout=1)
        for attempt in range(180):
            try:
                client.list_buckets()
                cache.ping()
                with socket.create_connection(("127.0.0.1", clamav), timeout=1) as connection:
                    connection.settimeout(1)
                    connection.sendall(b"zPING\0")
                    assert connection.recv(1024).startswith(b"PONG")
                break
            except (OSError, BotoCoreError, ClientError, AssertionError, ConnectionError):
                if attempt % 15 == 0:
                    print(f"Waiting for real file services ({attempt}s)", flush=True)
                time.sleep(1)
        else:
            raise RuntimeError("Knowledge services did not become ready")
        from app.config import Settings, StorageProfile
        from app.storage import ObjectStore

        ObjectStore(Settings(app_env="test", storage=StorageProfile(**storage))).initialize()
        yield {
            "STORAGE": json.dumps(storage),
            "CLAMAV_HOST": "127.0.0.1",
            "CLAMAV_PORT": str(clamav),
            "REDIS_URL": redis_url,
            "XFLJ_TEST_KNOWLEDGE_SERVICES": "1",
        }
    finally:
        for name in reversed(names):
            subprocess.run(["docker", "stop", "--time", "3", name], check=False, stdout=subprocess.DEVNULL)
