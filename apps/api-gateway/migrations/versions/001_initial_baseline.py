"""initial baseline

Revision ID: 001
Revises:
Create Date: 2026-03-15 15:30:00.000000

This is the baseline migration representing the current database state.
All tables and columns that existed before Alembic was introduced.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # This is a baseline migration - the tables already exist
    # No actions needed, just marks the starting point
    pass


def downgrade() -> None:
    # To downgrade, we would drop all tables
    # WARNING: This would delete all data!
    pass
