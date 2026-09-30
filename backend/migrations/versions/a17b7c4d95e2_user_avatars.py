"""Store private, normalized account avatars.

Revision ID: a17b7c4d95e2
Revises: f2b8a901d642
"""

import sqlalchemy as sa
from alembic import op

revision = "a17b7c4d95e2"
down_revision = "f2b8a901d642"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "user_avatars",
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("image", sa.LargeBinary(), nullable=False),
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "user_id"),
        sa.ForeignKeyConstraint(["tenant_id", "user_id"], ["users.tenant_id", "users.id"]),
    )
    op.create_index("ix_user_avatars_tenant_id", "user_avatars", ["tenant_id"])


def downgrade():
    op.drop_index("ix_user_avatars_tenant_id", table_name="user_avatars")
    op.drop_table("user_avatars")
