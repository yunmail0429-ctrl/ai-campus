"""Keep account references when an administrator deletes a teacher account.

Revision ID: f2b8a901d642
Revises: e076fa6f2c36
"""

import sqlalchemy as sa
from alembic import op

revision = "f2b8a901d642"
down_revision = "e076fa6f2c36"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_column("users", "deleted_at")
