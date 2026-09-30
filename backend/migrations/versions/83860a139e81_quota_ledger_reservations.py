"""quota_ledger_reservations

Revision ID: 83860a139e81
Revises: 8c26d7220041
"""

import sqlalchemy as sa
from alembic import op

revision = "83860a139e81"
down_revision = "8c26d7220041"
branch_labels = None
depends_on = None


def upgrade():
    sqlite = op.get_bind().dialect.name == "sqlite"
    op.add_column("quota_accounts", sa.Column("reserved", sa.Integer(), nullable=False, server_default="0"))
    op.add_column(
        "quota_accounts", sa.Column("frozen", sa.Boolean(), nullable=False, server_default=sa.false())
    )
    with op.batch_alter_table(
        "quota_accounts",
        table_args=(sa.CheckConstraint("balance >= 0"),) if sqlite else (),
    ) as batch:
        batch.alter_column(
            "reserved", existing_type=sa.Integer(), existing_nullable=False, server_default=None
        )
        batch.alter_column("frozen", existing_type=sa.Boolean(), existing_nullable=False, server_default=None)
        batch.create_unique_constraint("uq_quota_account_tenant_id", ["tenant_id", "id"])
        batch.create_check_constraint("ck_quota_reserved", "reserved >= 0 AND reserved <= balance")
    op.create_table(
        "quota_reservations",
        sa.Column("account_id", sa.String(length=36), nullable=False),
        sa.Column("operation_key", sa.String(length=100), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("settled_amount", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("reason", sa.String(length=300), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('reserved', 'settled', 'released')"),
        sa.CheckConstraint("amount > 0 AND settled_amount >= 0 AND settled_amount <= amount"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "account_id"],
            ["quota_accounts.tenant_id", "quota_accounts.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "account_id", "id"),
        sa.UniqueConstraint("tenant_id", "account_id", "operation_key"),
    )
    op.create_index(
        op.f("ix_quota_reservations_tenant_id"), "quota_reservations", ["tenant_id"], unique=False
    )
    op.create_table(
        "quota_transactions",
        sa.Column("account_id", sa.String(length=36), nullable=False),
        sa.Column("reservation_id", sa.String(length=36), nullable=True),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("operation_key", sa.String(length=100), nullable=False),
        sa.Column("balance_delta", sa.Integer(), nullable=False),
        sa.Column("reserved_delta", sa.Integer(), nullable=False),
        sa.Column("balance_after", sa.Integer(), nullable=False),
        sa.Column("reserved_after", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=300), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("kind IN ('adjustment', 'reserve', 'settle', 'release')"),
        sa.CheckConstraint("balance_after >= 0 AND reserved_after >= 0 AND reserved_after <= balance_after"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "account_id", "reservation_id"],
            ["quota_reservations.tenant_id", "quota_reservations.account_id", "quota_reservations.id"],
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "account_id"],
            ["quota_accounts.tenant_id", "quota_accounts.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "account_id", "kind", "operation_key"),
    )
    op.create_index(
        op.f("ix_quota_transactions_account_id"), "quota_transactions", ["account_id"], unique=False
    )
    op.create_index(
        op.f("ix_quota_transactions_tenant_id"), "quota_transactions", ["tenant_id"], unique=False
    )
    op.execute("""
        INSERT INTO quota_transactions (
            id, tenant_id, account_id, reservation_id, kind, operation_key,
            balance_delta, reserved_delta, balance_after, reserved_after, reason, created_at, updated_at
        ) SELECT id, tenant_id, id, NULL, 'adjustment', 'opening-balance',
            balance, 0, balance, 0, '历史余额迁移', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
          FROM quota_accounts WHERE balance > 0
    """)


def downgrade():
    outstanding = op.get_bind().scalar(sa.text("SELECT COUNT(*) FROM quota_accounts WHERE reserved > 0"))
    if outstanding:
        raise RuntimeError("Release or settle active reservations before downgrading")
    op.drop_index(op.f("ix_quota_transactions_tenant_id"), table_name="quota_transactions")
    op.drop_index(op.f("ix_quota_transactions_account_id"), table_name="quota_transactions")
    op.drop_table("quota_transactions")
    op.drop_index(op.f("ix_quota_reservations_tenant_id"), table_name="quota_reservations")
    op.drop_table("quota_reservations")
    sqlite = op.get_bind().dialect.name == "sqlite"
    with op.batch_alter_table(
        "quota_accounts",
        table_args=(sa.CheckConstraint("balance >= 0"),) if sqlite else (),
    ) as batch:
        batch.drop_constraint("ck_quota_reserved", type_="check")
        batch.drop_constraint("uq_quota_account_tenant_id", type_="unique")
        batch.drop_column("frozen")
        batch.drop_column("reserved")
