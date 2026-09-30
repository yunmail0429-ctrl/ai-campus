"""Rebuildable Qdrant mirror for dense and multilingual BM25 retrieval."""

from uuid import NAMESPACE_URL, uuid5

import httpx


class QdrantUnavailable(Exception):
    pass


class QdrantIndex:
    def __init__(self, settings, dimensions):
        if not settings.qdrant_url:
            raise QdrantUnavailable("Qdrant is not configured")
        self.collection = f"xflj_chunks_{dimensions}"
        self.root = settings.qdrant_url.rstrip("/")
        self.headers = {}
        if settings.qdrant_api_key:
            self.headers["api-key"] = settings.qdrant_api_key.get_secret_value()

    def request(self, method, path, data=None):
        try:
            with httpx.Client(timeout=httpx.Timeout(15, connect=3), trust_env=False) as client:
                response = client.request(method, self.root + path, json=data, headers=self.headers)
            if response.status_code >= 400:
                raise QdrantUnavailable(f"Qdrant returned {response.status_code}")
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise QdrantUnavailable("Qdrant request failed") from exc

    def collection_exists(self):
        path = f"/collections/{self.collection}"
        try:
            with httpx.Client(timeout=3, trust_env=False) as client:
                result = client.get(self.root + path, headers=self.headers)
        except httpx.HTTPError as exc:
            raise QdrantUnavailable("Qdrant request failed") from exc
        if result.status_code == 404:
            return False
        if result.status_code >= 400:
            raise QdrantUnavailable(f"Qdrant returned {result.status_code}")
        return True

    def ensure_collection(self, dimensions):
        if self.collection_exists():
            return False
        path = f"/collections/{self.collection}"
        self.request(
            "PUT", path,
            {
                "vectors": {"dense": {"size": dimensions, "distance": "Cosine", "on_disk": True}},
                "sparse_vectors": {"bm25": {"modifier": "idf", "index": {"on_disk": True}}},
                "on_disk_payload": True,
            },
        )
        for field in ("tenant_id", "index_key", "document_id"):
            self.request(
                "PUT", path + "/index?wait=true", {"field_name": field, "field_schema": "keyword"}
            )
        return True

    @staticmethod
    def point_id(tenant, key, chunk_id):
        return str(uuid5(NAMESPACE_URL, f"{tenant}:{key}:{chunk_id}"))

    @staticmethod
    def bm25(text):
        return {
            "text": text,
            "model": "qdrant/bm25",
            "options": {"tokenizer": "multilingual", "stemmer": {"type": "none"}, "stopwords": []},
        }

    def upsert(self, tenant, key, document_id, rows, dimensions):
        self.ensure_collection(dimensions)
        points = [
            {
                "id": self.point_id(tenant, key, chunk.id),
                "vector": {"dense": [float(value) for value in vector.embedding],
                           "bm25": self.bm25(chunk.content)},
                "payload": {
                    "tenant_id": tenant,
                    "index_key": key,
                    "document_id": document_id,
                    "chunk_id": chunk.id,
                },
            }
            for chunk, vector in rows
        ]
        for start in range(0, len(points), 16):
            batch = points[start : start + 16]
            self.request(
                "PUT", f"/collections/{self.collection}/points?wait=true", {"points": batch}
            )
            readback = self.request(
                "POST", f"/collections/{self.collection}/points",
                {"ids": [point["id"] for point in batch], "with_payload": False, "with_vector": False},
            )
            if {item["id"] for item in readback.get("result", [])} != {point["id"] for point in batch}:
                raise QdrantUnavailable("Qdrant readback mismatch")

    def hybrid(self, tenant, key, query, vector, limit=30, min_score=0.0):
        scope = {
            "must": [
                {"key": "tenant_id", "match": {"value": tenant}},
                {"key": "index_key", "match": {"value": key}},
            ]
        }
        data = {
            "prefetch": [
                {"query": vector, "using": "dense", "filter": scope, "limit": 20,
                 "score_threshold": min_score},
                {"query": self.bm25(query), "using": "bm25", "filter": scope, "limit": 20},
            ],
            "query": {"fusion": "rrf"},
            "limit": limit,
            "with_payload": True,
        }
        result = self.request("POST", f"/collections/{self.collection}/points/query", data)
        return [
            point["payload"]["chunk_id"]
            for point in result.get("result", {}).get("points", [])
            if point.get("payload", {}).get("tenant_id") == tenant
            and point["payload"].get("index_key") == key
            and isinstance(point["payload"].get("chunk_id"), str)
        ]

    def delete_document(self, tenant, document_id):
        if not self.collection_exists():
            return
        scope = {
            "must": [
                {"key": "tenant_id", "match": {"value": tenant}},
                {"key": "document_id", "match": {"value": document_id}},
            ]
        }
        self.request("POST", f"/collections/{self.collection}/points/delete?wait=true", {"filter": scope})

    def delete_other_versions(self, tenant, document_id, key):
        if not self.collection_exists():
            return
        scope = {
            "must": [
                {"key": "tenant_id", "match": {"value": tenant}},
                {"key": "document_id", "match": {"value": document_id}},
            ],
            "must_not": [{"key": "index_key", "match": {"value": key}}],
        }
        self.request("POST", f"/collections/{self.collection}/points/delete?wait=true", {"filter": scope})


def sync_ready_documents(sessions, settings, tenant):
    """Rebuild missing mirror points from committed SQL vectors without blocking normal indexing."""
    if not settings.qdrant_url:
        return
    from sqlalchemy import and_, or_, update

    from .embeddings import active_config, index_key
    from .models import ChunkVector, Document, DocumentChunk, IndexJob
    from .repository import Repository

    with sessions() as session:
        repo = Repository(session, tenant)
        spec = active_config(repo)
        if not spec:
            return
        key = index_key(settings, spec)
    mirror = QdrantIndex(settings, spec["dimensions"])
    try:
        created = mirror.ensure_collection(spec["dimensions"])
    except QdrantUnavailable:
        return
    if created:
        with sessions.begin() as session:
            session.execute(
                update(Document)
                .where(Document.tenant_id == tenant, Document.qdrant_synced_key == key)
                .values(qdrant_synced_key=None)
            )
    with sessions() as session:
        repo = Repository(session, tenant)
        identities = list(
            session.scalars(
                repo.query(Document)
                .join(IndexJob, and_(IndexJob.tenant_id == tenant, IndexJob.document_id == Document.id))
                .where(
                    Document.deleted_at.is_(None),
                    Document.status == "ready",
                    IndexJob.status == "succeeded",
                    IndexJob.index_key == key,
                    or_(Document.qdrant_synced_key.is_(None), Document.qdrant_synced_key != key),
                )
                .with_only_columns(Document.id)
                .limit(1)
            )
        )
    for identity in identities:
        with sessions() as session:
            repo = Repository(session, tenant)
            rows = list(
                session.execute(
                    repo.query(ChunkVector)
                    .join(
                        DocumentChunk,
                        and_(
                            DocumentChunk.tenant_id == tenant,
                            DocumentChunk.document_id == ChunkVector.document_id,
                            DocumentChunk.position == ChunkVector.position,
                        ),
                    )
                    .where(ChunkVector.document_id == identity, ChunkVector.index_key == key)
                    .with_only_columns(DocumentChunk, ChunkVector)
                    .order_by(DocumentChunk.position)
                )
            )
            expected = repo.one(Document, Document.id == identity).chunk_count
            if len(rows) != expected or not rows:
                continue
            try:
                mirror.upsert(tenant, key, identity, rows, spec["dimensions"])
            except QdrantUnavailable:
                return
        with sessions.begin() as session:
            repo = Repository(session, tenant)
            document = repo.one(Document, Document.id == identity, lock=True)
            job = repo.one(IndexJob, IndexJob.document_id == identity)
            if document and not document.deleted_at and job and job.index_key == key and job.status == "succeeded":
                document.qdrant_synced_key = key
                document.qdrant_dimensions = sorted(set((document.qdrant_dimensions or []) + [spec["dimensions"]]))
        try:
            mirror.delete_other_versions(tenant, identity, key)
        except QdrantUnavailable:
            pass
