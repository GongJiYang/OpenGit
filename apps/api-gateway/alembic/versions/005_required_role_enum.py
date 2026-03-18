"""
required_role enum + data coercion and CHECK

Revision ID: 005
Revises: 004
Create Date: 2026-03-18 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '005'
down_revision: Union[str, None] = '004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ALLOWED = ('architect','contributor','reviewer','executor','tester','librarian','observer')


def upgrade() -> None:
    conn = op.get_bind()

    # Normalize to lowercase
    conn.execute(sa.text("""
        UPDATE bounty
        SET required_role = LOWER(required_role)
        WHERE required_role IS NOT NULL
    """))

    # Coerce unknowns to 'contributor'
    conn.execute(sa.text("""
        UPDATE bounty
        SET required_role = 'contributor'
        WHERE required_role IS NULL
           OR TRIM(required_role) = ''
           OR required_role NOT IN :allowed
    """), {"allowed": ALLOWED})

    # Add CHECK constraint (SQLite-friendly)
    # Note: SQLite supports CHECK with IN list
    op.create_check_constraint(
        constraint_name="ck_bounty_required_role",
        table_name="bounty",
        condition=f"required_role IN {ALLOWED}"
    )


def downgrade() -> None:
    # Drop CHECK constraint
    op.drop_constraint("ck_bounty_required_role", "bounty", type_="check")
