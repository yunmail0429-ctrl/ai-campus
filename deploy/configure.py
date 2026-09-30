"""Create private local deployment configuration once, never print credentials."""

import json
import secrets
from pathlib import Path

root = Path(__file__).resolve().parent
if any(root.glob("*.env")):
    raise SystemExit("Configuration already exists; refusing to overwrite credentials.")
db, cache, storage = (secrets.token_hex(24) for _ in range(3))
admin = secrets.token_urlsafe(24)
values = {
    "admin.env": {"INITIAL_ADMIN_PASSWORD": admin},
    "postgres.env": {
        "POSTGRES_USER": "xflj",
        "POSTGRES_DB": "xflj",
        "POSTGRES_PASSWORD": db,
    },
    "redis.env": {"REDIS_PASSWORD": cache},
    "storage.env": {"MINIO_ROOT_USER": "xflj-storage", "MINIO_ROOT_PASSWORD": storage},
    "backend.env": {
        "APP_ENV": "local",
        "DATABASE_URL": f"postgresql+psycopg://xflj:{db}@postgres:5432/xflj",
        "ALLOWED_ORIGINS": json.dumps(["http://localhost:3100"]),
        "REDIS_URL": f"redis://:{cache}@redis:6379/0",
        "CLAMAV_HOST": "antivirus",
        "STORAGE": json.dumps(
            {
                "bucket": "campus-private",
                "endpoint_url": "http://storage:9000",
                "public_endpoint_url": "http://localhost:19000",
                "access_key": "xflj-storage",
                "secret_key": storage,
            }
        ),
    },
}
for name, settings in values.items():
    (root / name).write_text(
        "".join(f"{key}={value}\n" for key, value in settings.items()), encoding="utf-8"
    )
print("Local deployment configuration created.")
(root / "credentials.txt").write_text(
    f"地址：http://localhost:3100\n账号：admin\n初始密码：{admin}\n", encoding="utf-8"
)
