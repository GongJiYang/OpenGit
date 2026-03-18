"""
add cancel metadata to bounty

Revision ID: 004
Revises: 003
Create Date: 2026-03-18 10:20:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '004'
down_revision: Union[str, None] = '003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('bounty', sa.Column('cancelled_by', sa.String(), nullable=True))
    op.add_column('bounty', sa.Column('cancelled_reason', sa.String(), nullable=True))
    op.add_column('bounty', sa.Column('cancelled_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column('bounty', 'cancelled_at')
    op.drop_column('bounty', 'cancelled_reason')
    op.drop_column('bounty', 'cancelled_by')
