"""drop_uq_binding_user_add_agent_soft_delete

Revision ID: a1b2c3d4e5f6
Revises: 3f1e2a9d8c7b
Create Date: 2026-04-05 12:00:00.000000

Changes:
1. Drop uq_binding_user unique constraint from user_agent_bindings
   (allows one Passport to bind multiple Agents with different roles)
2. Add deleted_at and deleted_by columns to agents table
   (soft-delete support for Agent lifecycle management)
3. Add DELETED value to agentstatus enum
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '3f1e2a9d8c7b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Task 1: Drop uq_binding_user constraint ──────────────────────────
    # One Passport can now bind multiple Agents (different roles).
    # uq_binding_agent is preserved: each Agent still belongs to at most one User.
    # Use batch mode for SQLite compatibility
    with op.batch_alter_table('user_agent_bindings') as batch_op:
        batch_op.drop_constraint('uq_binding_user', type_='unique')

    # ── Task 2: Add soft-delete fields to agents ─────────────────────────
    op.add_column('agents', sa.Column('deleted_at', sa.DateTime(), nullable=True))
    op.add_column('agents', sa.Column('deleted_by', sa.String(length=50), nullable=True))

    # ── Task 3: Extend agentstatus enum with DELETED ─────────────────────
    # PostgreSQL requires ALTER TYPE to add enum values.
    # SQLite ignores this (enum stored as VARCHAR).
    # Only execute on PostgreSQL
    if op.get_context().dialect.name != 'sqlite':
        op.execute("ALTER TYPE agentstatus ADD VALUE IF NOT EXISTS 'DELETED'")


def downgrade() -> None:
    # ── Reverse Task 2: Remove soft-delete fields ─────────────────────────
    op.drop_column('agents', 'deleted_by')
    op.drop_column('agents', 'deleted_at')

    # ── Reverse Task 1: Recreate uq_binding_user constraint ───────────────
    # NOTE: This will fail if any user currently has multiple bindings.
    # Ensure data is cleaned up before running downgrade.
    # Use batch mode for SQLite compatibility
    with op.batch_alter_table('user_agent_bindings') as batch_op:
        batch_op.create_unique_constraint('uq_binding_user', ['user_id'])

    # NOTE: PostgreSQL does not support removing enum values.
    # The DELETED enum value cannot be removed via downgrade.
    # If rollback is needed, ensure no agents have status='DELETED' first.
