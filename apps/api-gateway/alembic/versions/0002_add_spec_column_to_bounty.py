"""add_spec_column_to_bounty

Revision ID: 0002_add_spec_column_to_bounty
Revises: a1b2c3d4e5f6
Create Date: 2026-05-01 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0002_add_spec_column_to_bounty'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("bounty", sa.Column("spec", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("bounty", "spec")
