#!/usr/bin/env bash
# Local dev launcher — bypasses all auth, uses SQLite, no external services needed.
# Usage: bash dev.sh
# Simulate roles via: curl -H "X-Dev-Role: contributor" ...

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

export PYTHONPATH="$REPO_ROOT:$SCRIPT_DIR:$SCRIPT_DIR/src"

# --- Dev bypass ---
export DEV_BYPASS_AUTH=1
export APP_SECURITY_MODE=warn
export APP_GOVERNANCE_MODE=off

# --- Minimal required envs (dummy values, not used in bypass mode) ---
export JWT_SECRET=dev-local-only
export JWT_SECRET_KEY=dev-local-only
export WECHAT_TOKEN=dev-local-only
export INTERNAL_API_TOKEN=dev-local-only

# --- Database: SQLite, auto-created ---
export DATABASE_URL="sqlite:///$SCRIPT_DIR/agenthub_data/dev.db"
export ALLOW_SQLMODEL_CREATE_ALL=1

# --- Disable optional services ---
export APP_ENABLE_INDEXER=false
export APP_ENABLE_SANDBOX=false
export APP_SANDBOX_PROVIDER=disabled
export RUN_SCHEDULER=false

mkdir -p "$SCRIPT_DIR/agenthub_data/repos"

# Run migrations
cd "$SCRIPT_DIR"
echo "[dev] Running DB migrations..."
alembic upgrade head 2>/dev/null || python -c "
import os; os.environ['ALLOW_SQLMODEL_CREATE_ALL']='1'
import sys; sys.path.insert(0,'src')
from persistence import create_db_and_tables; create_db_and_tables()
print('[dev] DB tables created via SQLModel fallback')
"

echo ""
echo "[dev] Starting API Gateway in DEV BYPASS mode"
echo "[dev] Auth is DISABLED — use X-Dev-Role header to simulate roles:"
echo "      architect   → create/manage bounties, decompose tasks"
echo "      contributor → claim and submit bounties"
echo "      reviewer    → review submissions"
echo ""
echo "[dev] API docs: http://localhost:8000/docs"
echo ""

uvicorn --app-dir "$SCRIPT_DIR" src.main:create_app \
    --factory --host 0.0.0.0 --port 8000 --reload
