from urllib.parse import parse_qs, urlsplit

import pytest
from moto import mock_aws
from pydantic import ValidationError

from app.config import Settings, StorageProfile
from app.storage import ObjectStore


def test_download_uses_public_endpoint_without_changing_internal_client():
    with mock_aws():
        store = ObjectStore(
            Settings(
                storage=StorageProfile(
                    bucket="private-test",
                    access_key="test",
                    secret_key="test",
                    public_endpoint_url="http://localhost:19000",
                )
            )
        )
        store.initialize()
        original = store.client.meta.endpoint_url
        url = urlsplit(store.download("private/document.txt", "教学.txt"))
        assert url.netloc == "localhost:19000"
        assert url.path == "/private-test/private/document.txt"
        assert parse_qs(url.query)["X-Amz-Expires"] == ["120"]
        assert "X-Amz-Signature" in parse_qs(url.query)
        assert store.client.meta.endpoint_url == original


@pytest.mark.parametrize("endpoint", ["https://user:secret@example.com", "https://example.com/path"])
def test_public_storage_endpoint_rejects_credentials_and_paths(endpoint):
    with pytest.raises(ValidationError):
        StorageProfile(
            bucket="private-test", access_key="test", secret_key="test", public_endpoint_url=endpoint
        )


def test_production_rejects_plaintext_public_storage_endpoint():
    with pytest.raises(ValidationError):
        Settings(
            app_env="production",
            cookie_secure=True,
            database_url="postgresql://localhost/test",
            allowed_origins=["https://school.example"],
            storage=StorageProfile(
                bucket="private-test",
                access_key="test",
                secret_key="test",
                endpoint_url="https://storage.example",
                public_endpoint_url="http://localhost:19000",
            ),
        )
