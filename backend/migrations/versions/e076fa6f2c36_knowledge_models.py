"""Allow per-school vision and reranking models.

Revision ID: e076fa6f2c36
Revises: c0bcb30a0942
"""

import sqlalchemy as sa
from alembic import op

revision = "e076fa6f2c36"
down_revision = "c0bcb30a0942"
branch_labels = None
depends_on = None


def upgrade():
    for name, length in (
        ("vision_provider", 80), ("vision_model", 120),
        ("rerank_provider", 80), ("rerank_model", 120),
    ):
        op.add_column("embedding_configs", sa.Column(name, sa.String(length), nullable=True))


def downgrade():
    for name in ("rerank_model", "rerank_provider", "vision_model", "vision_provider"):
        op.drop_column("embedding_configs", name)
