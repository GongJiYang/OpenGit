"""add compute job tokens and other fields

Revision ID: 002
Revises: 001
Create Date: 2026-03-15 15:30:00.000000

Adds missing columns that were manually added:
- compute_jobs: service_endpoint, access_token, token_expires_at
- bounty: repo_id
- agents: skills, preferred_tracks, max_concurrent_tasks, claimed_by_user_id
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '002'
down_revision: Union[str, None] = '001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add columns to compute_jobs table
    op.add_column('compute_jobs',
        sa.Column('service_endpoint', sa.String(), nullable=True)
    )
    op.add_column('compute_jobs',
        sa.Column('access_token', sa.String(), nullable=True)
    )
    op.add_column('compute_jobs',
        sa.Column('token_expires_at', sa.DateTime(), nullable=True)
    )

    # Add column to bounty table
    op.add_column('bounty',
        sa.Column('repo_id', sa.String(), nullable=True)
    )

    # Add columns to agents table
    op.add_column('agents',
        sa.Column('skills', sa.JSON(), nullable=True)
    )
    op.add_column('agents',
        sa.Column('preferred_tracks', sa.JSON(), nullable=True)
    )
    op.add_column('agents',
        sa.Column('max_concurrent_tasks', sa.Integer(), nullable=True, server_default='3')
    )
    op.add_column('agents',
        sa.Column('claimed_by_user_id', sa.String(), nullable=True)
    )


def downgrade() -> None:
    # Remove columns from compute_jobs table
    op.drop_column('compute_jobs', 'token_expires_at')
    op.drop_column('compute_jobs', 'access_token')
    op.drop_column('compute_jobs', 'service_endpoint')

    # Remove column from bounty table
    op.drop_column('bounty', 'repo_id')

    # Remove columns from agents table
    op.drop_column('agents', 'claimed_by_user_id')
    op.drop_column('agents', 'max_concurrent_tasks')
    op.drop_column('agents', 'preferred_tracks')
    op.drop_column('agents', 'skills')
