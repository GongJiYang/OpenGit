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


def upgrade() -> None:
    op.add_column("runners", sa.Column("token_lookup", sa.String(length=64), nullable=True))
    op.create_index(op.f("ix_runners_token_lookup"), "runners", ["token_lookup"], unique=True)

    op.add_column("runner_tokens", sa.Column("token_lookup", sa.String(length=64), nullable=True))
    op.create_index(op.f("ix_runner_tokens_token_lookup"), "runner_tokens", ["token_lookup"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_runner_tokens_token_lookup"), table_name="runner_tokens")
    op.drop_column("runner_tokens", "token_lookup")

    op.drop_index(op.f("ix_runners_token_lookup"), table_name="runners")
    op.drop_column("runners", "token_lookup")
