#!/usr/bin/env bash
# 一键运行所有测试（Docker 容器内）
# 使用方式：bash test.sh [api|ui|all]

set -euo pipefail
cd "$(dirname "$0")"

MODE="${1:-all}"
COMPOSE="docker compose -f docker-compose.test.yml"

case "$MODE" in
  api)
    echo "🧪 运行后端单测..."
    $COMPOSE up --build --abort-on-container-exit api-test
    ;;
  ui)
    echo "🎨 验证前端构建..."
    $COMPOSE up --build --abort-on-container-exit ui-test
    ;;
  all|*)
    echo "🚀 运行全部测试（后端 + 前端）..."
    $COMPOSE up --build --abort-on-container-exit
    ;;
esac

EXIT_CODE=$?
$COMPOSE down --remove-orphans 2>/dev/null || true

if [ $EXIT_CODE -eq 0 ]; then
  echo "✅ 所有测试通过"
else
  echo "❌ 测试失败，exit code: $EXIT_CODE"
fi

exit $EXIT_CODE
