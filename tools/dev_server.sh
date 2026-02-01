#!/bin/bash
# AgentHub Dev Server Launcher

# Ensure we are in the project root
cd "$(dirname "$0")/.."
PROJECT_ROOT=$(pwd)

echo "🚀 Starting AgentHub Backend..."
echo "📂 Root: $PROJECT_ROOT"

# 1. Setup Python Env
if [ ! -d ".venv" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv .venv
fi

source .venv/bin/activate

# 2. Install Dependencies (FastAPI, Uvicorn, etc.)
echo "📦 Checking dependencies..."
# Added --trusted-host flags to handle potential SSL issues
pip install -q fastapi uvicorn pydantic numpy gitpython httpx --trusted-host pypi.org --trusted-host files.pythonhosted.org

# 3. Configure Path (Monorepo Hack)
export PYTHONPATH=$PROJECT_ROOT/packages/protocol/src:$PYTHONPATH
export PYTHONPATH=$PROJECT_ROOT/services/git-core/src:$PYTHONPATH
export PYTHONPATH=$PROJECT_ROOT/services/semantic-store/src:$PYTHONPATH
export PYTHONPATH=$PROJECT_ROOT/services/execution-vmm/src:$PYTHONPATH
export PYTHONPATH=$PROJECT_ROOT/apps/api-gateway/src:$PYTHONPATH

# 4. Run Server
echo "🔥 API Gateway running on http://127.0.0.1:8000"
echo "Press Ctrl+C to stop."
python3 -m uvicorn main:app --app-dir apps/api-gateway/src --reload --port 8000
