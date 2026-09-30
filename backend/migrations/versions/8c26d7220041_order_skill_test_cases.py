"""Preserve the order of submitted Skill test cases."""

import sqlalchemy as sa
from alembic import op

revision = "8c26d7220041"
down_revision = "6a116a1a12f0"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("skill_test_cases", sa.Column("position", sa.Integer(), nullable=False, server_default="0"))
    op.execute("""
        UPDATE skill_test_cases SET position = (
            SELECT COUNT(*) FROM skill_test_cases AS older
            WHERE older.tenant_id = skill_test_cases.tenant_id
              AND older.skill_id = skill_test_cases.skill_id
              AND (older.created_at < skill_test_cases.created_at OR
                  (older.created_at = skill_test_cases.created_at AND older.id < skill_test_cases.id))
        )
    """)
    with op.batch_alter_table("skill_test_cases") as batch:
        batch.alter_column(
            "position", existing_type=sa.Integer(), existing_nullable=False, server_default=None
        )
        batch.create_unique_constraint("uq_skill_test_case_position", ["tenant_id", "skill_id", "position"])


def downgrade():
    with op.batch_alter_table("skill_test_cases") as batch:
        batch.drop_constraint("uq_skill_test_case_position", type_="unique")
        batch.drop_column("position")
