"""answer_feedback

Revision ID: c6a55d0b4045
Revises: 05fffabb1d01
"""

from alembic import op
import sqlalchemy as sa

revision = "c6a55d0b4045"
down_revision = "05fffabb1d01"
branch_labels = None
depends_on = None


def existing_checks():
    # SQLite batch recreation does not reflect unnamed CHECK constraints.
    return (
        sa.CheckConstraint("status IN ('running', 'completed', 'failed', 'cancelled')"),
        sa.CheckConstraint("input_tokens >= 0 AND output_tokens >= 0 AND credits >= 0"),
    )


def upgrade():
    op.add_column("model_invocations", sa.Column("feedback_rating", sa.Integer(), nullable=True))
    op.add_column("model_invocations", sa.Column("feedback_comment", sa.Text(), nullable=True))
    op.add_column("model_invocations", sa.Column("feedback_at", sa.DateTime(timezone=True), nullable=True))
    with op.batch_alter_table("model_invocations", table_args=existing_checks()) as batch:
        batch.create_check_constraint(
            "ck_feedback_rating", "feedback_rating IS NULL OR feedback_rating BETWEEN 1 AND 5"
        )


def downgrade():
    with op.batch_alter_table("model_invocations", table_args=existing_checks()) as batch:
        batch.drop_constraint("ck_feedback_rating", type_="check")
    op.drop_column("model_invocations", "feedback_at")
    op.drop_column("model_invocations", "feedback_comment")
    op.drop_column("model_invocations", "feedback_rating")
