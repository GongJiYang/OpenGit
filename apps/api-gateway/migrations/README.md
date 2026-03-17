# Alembic Database Migration Guide

## What is Alembic?

Alembic is a database migration tool for SQLAlchemy/SQLModel projects. It tracks database schema changes and allows you to apply or rollback changes in a controlled manner.

## Project Structure

```
api-gateway/
├── alembic.ini              # Alembic configuration
├── migrations/
│   ├── env.py               # Migration environment (runs SQL)
│   ├── versions/            # Migration scripts
│   │   ├── 001_initial_baseline.py
│   │   └── 002_add_compute_job_tokens_and_other_fields.py
│   └── README               # This file
└── src/
    └── ...
```

## Current Migration Status

| Version | Description | Status |
|---------|-------------|--------|
| 001 | Initial baseline (marks pre-Alembic state) | ✅ Applied |
| 002 | Added token fields and other missing columns | ✅ Applied |

## Common Commands

### Check current migration version
```bash
alembic current
```

### Show migration history
```bash
alembic history
```

### Create a new migration (manual)

Since this project has complex dependencies, we use **manual migrations** instead of autogenerate:

1. Create a new file in `migrations/versions/`:
   ```bash
   # Format: <3-digit revision>_<description>.py
   # Example: 003_add_user_preferences.py
   ```

2. Use this template:
   ```python
   """add user preferences

   Revision ID: 003
   Revises: 002
   Create Date: 2026-03-15
   """
   from typing import Sequence, Union
   from alembic import op
   import sqlalchemy as sa

   revision = '003'
   down_revision = '002'
   branch_labels = None
   depends_on = None

   def upgrade() -> None:
       # Add your changes here
       op.add_column('users', sa.Column('preferences', sa.JSON(), nullable=True))

   def downgrade() -> None:
       # Reverse the changes here
       op.drop_column('users', 'preferences')
   ```

3. Apply the migration:
   ```bash
   export DATABASE_URL="postgresql://..."
   alembic upgrade head
   ```

### Apply pending migrations
```bash
export DATABASE_URL="postgresql://..."
alembic upgrade head
```

### Rollback one migration
```bash
export DATABASE_URL="postgresql://..."
alembic downgrade -1
```

### Rollback to specific version
```bash
export DATABASE_URL="postgresql://..."
alembic downgrade 001
```

## GitOps Integration

### Option 1: Init Container (Recommended)

Add to your Deployment:

```yaml
spec:
  template:
    spec:
      initContainers:
      - name: run-migrations
        image: your-image:latest
        command: ['alembic', 'upgrade', 'head']
        env:
        - name: DATABASE_URL
          valueFrom:
            secretKeyRef:
              name: db-secret
              key: url
      containers:
      - name: app
        # Your main application
```

### Option 2: Kubernetes Job

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: migration-job
spec:
  template:
    spec:
      containers:
      - name: alembic
        image: your-image:latest
        command: ['alembic', 'upgrade', 'head']
        env:
        - name: DATABASE_URL
          value: "postgresql://..."
      restartPolicy: Never
```

## Important Notes

1. **Version IDs**: Must be unique and under 32 characters (use 3-digit numbers like `001`, `002`)

2. **Manual Migrations**: This project uses manual migrations because importing models triggers complex dependencies. Always write migration SQL manually.

3. **Testing**: Test migrations on a staging database before production!

4. **Backwards Compatibility**: Consider rollback scenarios when writing migrations.

5. **Never modify** existing migration files that have been applied. Create new migrations instead.
