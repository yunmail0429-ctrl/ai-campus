"""Reuse the current S3 credentials for a staged RustFS cutover."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
source = ROOT / "deploy/storage.env"
target = ROOT / "deploy/rustfs.env"
values = dict(
    line.split("=", 1) for line in source.read_text(encoding="utf-8").splitlines()
    if line and not line.startswith("#") and "=" in line
)
access, secret = values["MINIO_ROOT_USER"], values["MINIO_ROOT_PASSWORD"]
if not access or not secret or "\n" in access + secret or "\r" in access + secret:
    raise ValueError("Invalid source storage credentials")
target.write_text(
    f"RUSTFS_ACCESS_KEY={access}\nRUSTFS_SECRET_KEY={secret}\n"
    "RUSTFS_VOLUMES=/data\nRUSTFS_CONSOLE_ENABLE=false\n",
    encoding="utf-8",
)
print("Wrote RustFS environment with the existing S3 credentials")
