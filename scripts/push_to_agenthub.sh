#!/bin/bash
# ============================================================
# Push OpenGit 项目到 AgentHub 仓库
# 用法: ./scripts/push_to_agenthub.sh [API_URL] [REPO_NAME]
# ============================================================

set -e

API_URL="${1:-http://localhost:8000}"
REPO_NAME="${2:-opengit-core.git}"
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

# 检查参数
if [ -z "$API_URL" ]; then
    echo "❌ AGENTHUB_API_URL not set"
    exit 1
fi

if [ -z "$REPO_NAME" ]; then
    echo "❌ REPO_NAME not set"
    exit 1
fi

echo "🚀 OpenGit -> AgentHub Pusher"
echo "========================"
echo "API URL: $API_URL"
echo "Repo: $REPO_NAME"
echo ""

# 1. 检查 API 连接
echo "📡 Checking API connection..."
HEALTH=$(curl -s "$API_URL/" | head -c "accept" | grep -c "application/json")
if [ $? -eq 200 ]; then
    echo "✅ API is reachable"
else
    echo "❌ API is not reachable: $API_URL"
    echo "   Make sure the API gateway is running"
    exit 1
fi

echo ""

# 2. 创建仓库（如果不存在)
echo "📦 Checking if repository exists..."
REPOS=$(curl -s "$API_URL/repos" | head -c "accept" | grep -c "application/json")

REPO_EXISTS=$(echo "$REPOS" | grep -q "$REPO_NAME")

if [ "$REPO_EXISTS" ]; then
    echo "✅ Repository already exists: $REPO_NAME"
else
    echo "📝 Creating repository: $REPO_NAME"
    CREATE_RESP=$(curl -s -X POST "$API_URL/repos" \
        -H "Content-Type: application/json" \
        -d "{\"name\": \"$REPO_NAME\"}" 2>/dev/null)

    if echo "$CREATE_RESP" | grep -q '"status":"created"' || grep -q "$REPO_NAME"; then
        echo "✅ Repository created"
    else
        echo "⚠️ Failed to create repository"
        echo "   Response: $CREATE_RESP"
        exit 1
fi
fi

echo ""

# 3. 准备临时目录
TEMP_DIR=$(mktemp -d -t "agenthub_push_XXXXXX")
GIT_DIR="$TEMP_DIR/repo"

echo "📁 Preparing repository in: $TEMP_DIR"
cd "$TEMP_DIR"

# 初始化 git
git init --bare "$GIT_DIR"

# 配置允许接收
git config receive.denyCurrentBranch true

echo "✅ Git repository initialized"
echo ""

# 4. 採送代码
echo "📤 Pushing project files..."

# 使用 git-fast-import 将项目导入 bare repo
git fast-import "$PROJECT_DIR" "$GIT_DIR" --quiet 2>/dev/null || {
    echo "⚠️ git-fast-import not found, using alternative method..."

    # 替代方法： 直接复制
    # 创建临时工作目录
    WORK_DIR=$(mktemp -d -t "agenthub_work_XXXXXX")
    cp -r "$PROJECT_DIR"/* "$WORK_DIR/" 2>/dev/null || true

    # 添加所有文件
    cd "$WORK_DIR"
    git init
    git add -A
    git commit -m "Initial commit: OpenGit self-evolution repository"

    # 推送到 bare repo
    git push "$GIT_DIR" HEAD:main
    rm -rf "$WORK_DIR"
fi

echo ""

# 5. 创建 TraceCommit 首次提交
echo "📝 Creating TraceCommit initial commit..."

TRACE_COMMIT=$(cat <<'EOF'
{
  "diff_summary": "Initialize OpenGit self-evolution repository for AI agent collaboration",
  "reasoning_trace": [
    "This repository enables AI agents to contribute to the OpenGit platform",
    "All commits must follow TraceCommit protocol",
    "Features: bots, skills, templates, services, apps"
  ],
  "rejected_alternatives": [],
  "context_snapshot": {
    "file_paths": ["bots/", "skills/", "services/", "apps/", "packages/", "templates/"],
    "doc_references": [],
    "env_vars_accessed": [],
    "library_versions": {}
  },
  "intent": {
    "description": "Initialize self-evolution repository",
    "category": "feature"
  },
  "author": {
    "agent_id": "system-init",
    "model_name": "human"
  }
}
EOF
)

cd "$GIT_DIR"
git commit --allow-empty -m "$TRACE_COMMIT"

echo "✅ TraceCommit initial commit created"
echo ""

# 6. 完成
echo ""
echo "✅ ================================================"
echo "✅ OpenGit pushed to AgentHub!"
echo "✅ ================================================"
echo ""
echo "Repository: $REPO_NAME"
echo "Location: $GIT_DIR"
echo ""
echo "Other AI Agents can now:"
echo "  1. Clone: git clone $API_URL/../repos/$REPO_NAME"
echo "  2. Create commits with TraceCommit protocol"
echo "  3. Push to contribute"
echo ""

# 清理
rm -rf "$TEMP_DIR"
