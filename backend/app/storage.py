import json
from urllib.parse import quote

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from .errors import AppError


class ObjectStore:
    """Server-owned bucket and opaque keys only; callers must check document access."""

    def __init__(self, settings):
        profile = settings.storage
        if profile is None:
            raise AppError(503, "STORAGE_NOT_CONFIGURED", "文件存储尚未配置，请联系学校管理员。")
        self.profile = profile
        self.client = boto3.client(
            "s3",
            endpoint_url=profile.endpoint_url,
            region_name=profile.region,
            aws_access_key_id=profile.access_key.get_secret_value(),
            aws_secret_access_key=profile.secret_key.get_secret_value(),
            config=Config(
                signature_version="s3v4",
                connect_timeout=5,
                read_timeout=30,
                retries={"max_attempts": 1},
                s3={"addressing_style": "path"},
            ),
        )

    def initialize(self):
        try:
            try:
                self.client.head_bucket(Bucket=self.profile.bucket)
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") not in ("404", "NoSuchBucket", "NotFound"):
                    raise
                location = (
                    {"CreateBucketConfiguration": {"LocationConstraint": self.profile.region}}
                    if self.profile.region != "us-east-1"
                    else {}
                )
                self.client.create_bucket(Bucket=self.profile.bucket, **location)
            self.private_bucket()
        except (BotoCoreError, ClientError):
            raise AppError(503, "STORAGE_UNAVAILABLE", "无法初始化私有文件桶，请检查连接和授权。") from None

    def private_bucket(self):
        try:
            acl = self.client.get_bucket_acl(Bucket=self.profile.bucket)
            if any(grant.get("Grantee", {}).get("Type") == "Group" for grant in acl.get("Grants", [])):
                raise AppError(503, "STORAGE_NOT_PRIVATE", "文件桶未配置为私有，请联系管理员。")
            try:
                policy = json.loads(self.client.get_bucket_policy(Bucket=self.profile.bucket)["Policy"])
                statements = policy.get("Statement", [])
                if isinstance(statements, dict):
                    statements = [statements]
                if any(
                    row.get("Effect") == "Allow" and "*" in json.dumps(row.get("Principal", {}))
                    for row in statements
                ):
                    raise AppError(503, "STORAGE_NOT_PRIVATE", "文件桶存在公开授权，请联系管理员。")
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") not in ("NoSuchBucketPolicy", "NoSuchPolicy"):
                    raise
        except (BotoCoreError, ClientError, ValueError):
            raise AppError(503, "STORAGE_UNAVAILABLE", "无法确认私有文件存储状态，请联系管理员。") from None

    def put(self, key, content):
        self.private_bucket()
        args = {"ServerSideEncryption": self.profile.encryption} if self.profile.encryption else {}
        try:
            self.client.put_object(
                Bucket=self.profile.bucket,
                Key=key,
                Body=content,
                ContentType="application/octet-stream",
                **args,
            )
        except (BotoCoreError, ClientError):
            raise AppError(503, "STORAGE_UNAVAILABLE", "文件存储暂时不可用，请重试上传。") from None

    def read(self, key, limit):
        try:
            response = self.client.get_object(Bucket=self.profile.bucket, Key=key)
            with response["Body"] as body:
                content = body.read(limit + 1)
            if len(content) > limit:
                raise AppError(422, "FILE_TOO_LARGE", "文件超过处理大小限制。")
            return content
        except (BotoCoreError, ClientError):
            raise AppError(503, "STORAGE_UNAVAILABLE", "无法读取文件，请稍后重试。") from None

    def delete(self, key):
        try:
            self.client.delete_object(Bucket=self.profile.bucket, Key=key)
        except (BotoCoreError, ClientError):
            raise AppError(503, "STORAGE_UNAVAILABLE", "文件清理暂时不可用。") from None

    def download(self, key, filename):
        self.private_bucket()
        signer = self.client
        if self.profile.public_endpoint_url:
            signer = boto3.client(
                "s3",
                endpoint_url=self.profile.public_endpoint_url,
                region_name=self.profile.region,
                aws_access_key_id=self.profile.access_key.get_secret_value(),
                aws_secret_access_key=self.profile.secret_key.get_secret_value(),
                config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
            )
        return signer.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self.profile.bucket,
                "Key": key,
                "ResponseContentType": "application/octet-stream",
                "ResponseContentDisposition": f"attachment; filename=download; filename*=UTF-8''{quote(filename, safe='')}",
            },
            ExpiresIn=120,
        )
