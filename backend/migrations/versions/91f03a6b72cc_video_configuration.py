"""Administrator video model and credit configuration."""

import sqlalchemy as sa
from alembic import op

revision = "91f03a6b72cc"
down_revision = "8dde7b50e065"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "video_configs",
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id"),
    )
    op.create_index("ix_video_configs_tenant_id", "video_configs", ["tenant_id"])


def downgrade():
    op.drop_index("ix_video_configs_tenant_id", table_name="video_configs")
    op.drop_table("video_configs")
