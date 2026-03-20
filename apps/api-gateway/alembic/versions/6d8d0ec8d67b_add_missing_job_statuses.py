"""add_missing_job_statuses

Revision ID: 6d8d0ec8d67b
Revises: c6274e9707ba
Create Date: 2026-03-20 20:35:00.000000

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "6d8d0ec8d67b"
down_revision: Union[str, Sequence[str], None] = "c6274e9707ba"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add missing compute job status enum values for existing PostgreSQL databases."""
    context = op.get_context()
    if context.dialect.name != "postgresql":
        return

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_type WHERE typname = 'computejobstatus') THEN
                IF NOT EXISTS (
                    SELECT 1
                    FROM pg_enum e
                    JOIN pg_type t ON t.oid = e.enumtypid
                    WHERE t.typname = 'computejobstatus' AND e.enumlabel = 'PARTIAL_PASS'
                ) THEN
                    ALTER TYPE computejobstatus ADD VALUE 'PARTIAL_PASS';
                END IF;

                IF NOT EXISTS (
                    SELECT 1
                    FROM pg_enum e
                    JOIN pg_type t ON t.oid = e.enumtypid
                    WHERE t.typname = 'computejobstatus' AND e.enumlabel = 'HUMAN_REVIEW'
                ) THEN
                    ALTER TYPE computejobstatus ADD VALUE 'HUMAN_REVIEW';
                END IF;
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    """Enum value removal is intentionally a no-op for PostgreSQL safety."""
    pass
