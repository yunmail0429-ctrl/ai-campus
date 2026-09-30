"""Store ordered page content alongside retrieval chunks.

Revision ID: bc60a86499b1
Revises: 91f03a6b72cc
"""

import sqlalchemy as sa
from alembic import op

revision = "bc60a86499b1"
down_revision = "91f03a6b72cc"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "document_pages",
        sa.Column("document_id", sa.String(36), nullable=False),
        sa.Column("page_index", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(80), nullable=False),
        sa.Column("blocks", sa.JSON(), nullable=False),
        sa.Column("assembled_text", sa.Text(), nullable=False),
        sa.Column("needs_review", sa.Boolean(), nullable=False),
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "document_id"], ["documents.tenant_id", "documents.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint("tenant_id", "document_id", "page_index"),
    )
    op.create_index("ix_document_pages_tenant_id", "document_pages", ["tenant_id"])
    op.create_index("ix_document_pages_document_id", "document_pages", ["document_id"])


def downgrade():
    op.drop_index("ix_document_pages_document_id", table_name="document_pages")
    op.drop_index("ix_document_pages_tenant_id", table_name="document_pages")
    op.drop_table("document_pages")
