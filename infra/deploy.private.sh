#!/usr/bin/env bash
# 私有部署一键脚本
# 使用方式：bash deploy.private.sh

set -euo pipefail
cd "$(dirname "$0")"

ENV_FILE=".env.private"

# ── 检查 .env.private ───────────────────────────────
if [ ! -f "$ENV_FILE" ]; then
    echo "❌ 缺少 $ENV_FILE"
    echo "   请先执行：cp .env.private.example .env.private 并填入真实值"
    exit 1
fi

# 检查必填项是否还是默认值
if grep -q "change_me" "$ENV_FILE"; then
    echo "⚠️  $ENV_FILE 中仍有未修改的 change_me 占位符，请先填入真实值"
    exit 1
fi

echo "🔨 构建镜像..."
docker compose -f docker-compose.private.yml --env-file "$ENV_FILE" build

echo "🚀 启动服务..."
docker compose -f docker-compose.private.yml --env-file "$ENV_FILE" up -d

echo "⏳ 等待数据库就绪..."
sleep 5

echo "🗄️  执行数据库迁移..."
docker compose -f docker-compose.private.yml --env-file "$ENV_FILE" \
    exec api-gateway alembic -c /app/apps/api-gateway/alembic.ini upgrade head

echo ""
echo "✅ 私有部署完成"
echo "   访问地址：http://localhost:$(grep LISTEN_PORT "$ENV_FILE" | cut -d= -f2 || echo 80)"
echo "   API 文档：http://localhost:$(grep LISTEN_PORT "$ENV_FILE" | cut -d= -f2 || echo 80)/api/v1/docs"
