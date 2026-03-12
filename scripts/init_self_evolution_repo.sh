#!/bin/bash
# ============================================================
# OpenGit 自我迭代仓库初始化脚本
# 在 AgentHub 平台上创建 opengit-core 仓库
# 允许其他 AI Agent 协作开发本项目
# ============================================================

set -e

API_URL="${AGENTHUB_API_URL:-http://localhost:8000}"
API_KEY="${AGENT_API_KEY:-}"
REPO_NAME="opengit-core.git"
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "🚀 OpenGit Self-Evolution Repository Initializer"
echo "================================================"
echo "API URL: $API_URL"
echo "Repo Name: $REPO_NAME"
echo "Project Dir: $PROJECT_DIR"
echo ""

# 1. 创建仓库
echo "📦 Step 1: Creating repository on AgentHub..."
if [ -n "$API_KEY" ]; then
    AUTH_HEADER="-H \"X-API-Key: $API_KEY\""
else
    AUTH_HEADER=""
fi

RESPONSE=$(curl -s -X POST "$API_URL/repos" \
    -H "Content-Type: application/json" \
    $AUTH_HEADER \
    -d "{\"name\": \"$REPO_NAME\"}" 2>/dev/null || echo '{"error": "failed"}')

if echo "$RESPONSE" | grep -q '"status":"created"'; then
    echo "✅ Repository created: $REPO_NAME"
elif echo "$RESPONSE" | grep -q '"error"'; then
    echo "⚠️  Repository may already exist or creation failed"
    echo "   Response: $RESPONSE"
else
    echo "✅ Repository ready"
fi

# 2. 获取仓库路径
REPO_PATH=$(curl -s "$API_URL/repos" 2>/dev/null | grep -o "\"$REPO_NAME\"" | head -1)
if [ -z "$REPO_PATH" ]; then
    echo "❌ Repository not found in AgentHub"
    exit 1
fi

# 3. 初始化本地 git 并推送
echo ""
echo "📥 Step 2: Preparing local repository..."

# 创建临时目录用于推送
TEMP_DIR=$(mktemp -d)
cd "$TEMP_DIR"

# 克隆 AgentHub 仓库
echo "Cloning from AgentHub..."
git clone "$API_URL/../repos/$REPO_NAME" . 2>/dev/null || {
    # 如果克隆失败，初始化新仓库
    git init --bare
}

# 复制项目文件
echo "Copying project files..."
rm -rf .git/objects/pack/* 2>/dev/null || true
cp -r "$PROJECT_DIR"/* . 2>/dev/null || true
cp "$PROJECT_DIR"/.gitignore . 2>/dev/null || true

# 创建首次提交 (TraceCommit 格式)
echo ""
echo "📝 Step 3: Creating initial commit with TraceCommit protocol..."

COMMIT_MSG=$(cat <<'EOF'
{
  "diff_summary": "Initial commit: OpenGit self-evolution repository",
  "reasoning_trace": [
    "This repository enables AI agents to collaborate on improving OpenGit platform",
    "All commits must follow TraceCommit protocol",
    "Agents can contribute code, templates, skills, and infrastructure"
  ],
  "rejected_alternatives": [],
  "context_snapshot": {
    "file_paths": ["bots/", "skills/", "services/", "apps/", "packages/"],
    "doc_references": [],
    "env_vars_accessed": [],
    "library_versions": {}
  },
  "intent": {
    "description": "Initialize self-evolution repository for OpenGit platform",
    "category": "feature"
  },
  "author": {
    "agent_id": "system-init",
    "model_name": "human"
  }
}
EOF
)

git add -A
git commit -m "$COMMIT_MSG" --allow-empty-message 2>/dev/null || echo "Nothing to commit"

# 推送到 AgentHub
echo ""
echo "🚀 Step 4: Pushing to AgentHub..."
REMOTE_URL="$API_URL/../repos/$REPO_NAME"

# 配置远程
git remote remove origin 2>/dev/null || true
git remote add origin "$REMOTE_URL"

# 推送
git push -u origin HEAD:main --force 2>/dev/null || {
    echo "⚠️  Push failed. You may need to configure git access."
    echo "   Remote URL: $REMOTE_URL"
}

# 清理
cd "$PROJECT_DIR"
rm -rf "$TEMP_DIR"

echo ""
echo "✅ ================================================"
echo "✅ Self-Evolution Repository Initialized!"
echo "✅ ================================================"
echo ""
echo "Repository: $REPO_NAME"
echo "Other AI Agents can now:"
echo "  1. Clone: git clone $API_URL/../repos/$REPO_NAME"
echo "  2. Make changes with TraceCommit protocol"
echo "  3. Push contributions for review"
echo ""
