"""Track which document vectors have been mirrored to Qdrant.

Revision ID: c0bcb30a0942
Revises: bc60a86499b1
"""

import sqlalchemy as sa
from alembic import op

revision = "c0bcb30a0942"
down_revision = "bc60a86499b1"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("documents", sa.Column("qdrant_synced_key", sa.String(64), nullable=True))
    op.add_column("documents", sa.Column("qdrant_dimensions", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("documents", "qdrant_dimensions")
    op.drop_column("documents", "qdrant_synced_key")
