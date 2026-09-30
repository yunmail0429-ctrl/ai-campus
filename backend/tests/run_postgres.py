"""Run integration tests against a disposable PostgreSQL container with a random password."""

import json
import os
import secrets
import subprocess
import sys
import time
import uuid
from pathlib import Path

import psycopg


def main():
    root = Path(__file__).resolve().parents[1]
    container = "xflj-integration-" + uuid.uuid4().hex[:12]
    password = secrets.token_hex(24)
    environment = {**os.environ, "POSTGRES_PASSWORD": password}
    created = False
    try:
        subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "-d",
                "--name",
                container,
                "-e",
                "POSTGRES_PASSWORD",
                "-e",
                "POSTGRES_USER=xflj",
                "-e",
                "POSTGRES_DB=xflj",
                "-p",
                "127.0.0.1::5432",
                "pgvector/pgvector:pg17",
            ],
            env=environment,
            check=True,
            stdout=subprocess.DEVNULL,
        )
        created = True
        info = json.loads(subprocess.check_output(["docker", "inspect", container], text=True))[0]
        port = info["NetworkSettings"]["Ports"]["5432/tcp"][0]["HostPort"]
        dsn = f"postgresql://xflj:{password}@127.0.0.1:{port}/xflj"
        for _ in range(40):
            try:
                with psycopg.connect(dsn, connect_timeout=1):
                    break
            except psycopg.OperationalError:
                time.sleep(1)
        else:
            raise RuntimeError("PostgreSQL test container did not become ready")
        environment["TEST_DATABASE_URL"] = dsn.replace("postgresql://", "postgresql+psycopg://", 1)
        local = root / "local-data"
        local.mkdir(exist_ok=True)
        environment["PYTEST_DEBUG_TEMPROOT"] = str(local)
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "--tb=short"], cwd=root, env=environment, check=False
        )
        return result.returncode
    finally:
        if created:
            subprocess.run(["docker", "stop", container], check=False, stdout=subprocess.DEVNULL)


if __name__ == "__main__":
    raise SystemExit(main())
