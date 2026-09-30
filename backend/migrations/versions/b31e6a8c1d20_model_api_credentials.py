"""Store optional per-model API connection and encrypted credential."""

import sqlalchemy as sa
from alembic import op

revision = "b31e6a8c1d20"
down_revision = "a17b7c4d95e2"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("model_configs", sa.Column("api_base_url", sa.String(500), nullable=True))
    op.add_column("model_configs", sa.Column("encrypted_api_key", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("model_configs", "encrypted_api_key")
    op.drop_column("model_configs", "api_base_url")
