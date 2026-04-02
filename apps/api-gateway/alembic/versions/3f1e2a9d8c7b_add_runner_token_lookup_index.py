"""add_runner_token_lookup_index

Revision ID: 3f1e2a9d8c7b
Revises: 6d8d0ec8d67b
Create Date: 2026-03-27 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "3f1e2a9d8c7b"
down_revision: Union[str, Sequence[str], None] = "6d8d0ec8d67b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _sqlite_table_has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        return False
    rows = bind.exec_driver_sql(f"PRAGMA table_info({table_name})").fetchall()
    return any(str(row[1]) == column_name for row in rows)


def upgrade() -> None:
    op.add_column("runners", sa.Column("token_lookup", sa.String(length=64), nullable=True))
    op.create_index(op.f("ix_runners_token_lookup"), "runners", ["token_lookup"], unique=True)

    op.add_column("runner_tokens", sa.Column("token_lookup", sa.String(length=64), nullable=True))
    op.create_index(op.f("ix_runner_tokens_token_lookup"), "runner_tokens", ["token_lookup"], unique=True)
    op.drop_index(op.f("ix_runner_tokens_token"), table_name="runner_tokens")

    context = op.get_context()
    if context.dialect.name == "postgresql":
        op.drop_column("runner_tokens", "token")
    elif context.dialect.name == "sqlite" and _sqlite_table_has_column("runner_tokens", "token"):
        with op.batch_alter_table("runner_tokens") as batch_op:
            batch_op.drop_column("token")


def downgrade() -> None:
    context = op.get_context()
    if context.dialect.name == "postgresql":
        op.add_column("runner_tokens", sa.Column("token", sa.String(length=64), nullable=True))
    elif context.dialect.name == "sqlite" and not _sqlite_table_has_column("runner_tokens", "token"):
        with op.batch_alter_table("runner_tokens") as batch_op:
            batch_op.add_column(sa.Column("token", sa.String(length=64), nullable=True))

    op.create_index(op.f("ix_runner_tokens_token"), "runner_tokens", ["token"], unique=True)
    op.drop_index(op.f("ix_runner_tokens_token_lookup"), table_name="runner_tokens")
    op.drop_column("runner_tokens", "token_lookup")

    op.drop_index(op.f("ix_runners_token_lookup"), table_name="runners")
    op.drop_column("runners", "token_lookup")
