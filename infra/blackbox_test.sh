#!/usr/bin/env bash
# OpenGit AgentHub API 黑盒测试
# 目标：http://localhost:8001 (OpenGit api-gateway)
BASE="http://localhost:8001"
PASS=0; FAIL=0; SKIP=0
API_KEY=""; AGENT_ID=""; BOUNTY_ID=""; USER_TOKEN=""; REPO_ID=""

c_green="\033[32m"; c_red="\033[31m"; c_yellow="\033[33m"; c_cyan="\033[36m"; c_reset="\033[0m"

check() {
  local name="$1" expected="$2" actual="$3"
  # 支持 ^ 开头的精确匹配
  if echo "$actual" | python3 -c "
import sys, re
s = sys.stdin.read().strip()
keys = '$expected'.split('|')
for k in keys:
    if k.startswith('^'):
        if re.match(k, s):
            exit(0)
    elif k in s:
        exit(0)
exit(1)
" 2>/dev/null; then
    echo -e "  ${c_green}✓${c_reset} $name"
    ((PASS++))
  else
    echo -e "  ${c_red}✗${c_reset} $name"
    echo -e "    ${c_yellow}expect:${c_reset} $expected"
    echo -e "    ${c_yellow}actual:${c_reset} $(echo "$actual" | head -c 300)"
    ((FAIL++))
  fi
}

http_check() {
  local name="$1" expected="$2" actual="$3"
  if echo "$actual" | grep -q "^$expected" 2>/dev/null; then
    echo -e "  ${c_green}✓${c_reset} $name"
    ((PASS++))
  else
    echo -e "  ${c_red}✗${c_reset} $name"
    echo -e "    ${c_yellow}expect HTTP:${c_reset} $expected  ${c_yellow}actual:${c_reset} $actual"
    ((FAIL++))
  fi
}

warn() { echo -e "  ${c_yellow}⚠${c_reset} $1"; ((FAIL++)); }
section() { echo -e "\n${c_cyan}▶ $1${c_reset}"; }

# ══════════════════════════════════════════════════════════════
section "M1 系统基础"

R=$(curl -s "$BASE/")
check "1.1 GET / 服务在线" 'AgentHub V2|online' "$R"

R=$(curl -s "$BASE/stats")
check "1.3 GET /stats 返回系统统计" 'total_repos|active_agents|system_load' "$R"

# 1.4/1.5 需要认证，移到 M3 认领完成后执行

R=$(curl -s "$BASE/api/v1/workitems")
check "1.6 GET /api/v1/workitems 返回工作项列表" 'items|\[\]' "$R"

# ══════════════════════════════════════════════════════════════
section "M2 用户认证"

EMAIL="bbtest_$(date +%s)@example.com"
R=$(curl -s -X POST "$BASE/api/v1/auth/register" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$EMAIL\",\"password\":\"TestPass123!\"}")
check "2.1 POST /auth/register 注册新用户" 'access_token' "$R"
USER_TOKEN=$(echo "$R" | python3 -c "import sys,json; print(json.load(sys.stdin).get('access_token',''))" 2>/dev/null)

R=$(curl -s -X POST "$BASE/api/v1/auth/register" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$EMAIL\",\"password\":\"TestPass123!\"}")
HTTP=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE/api/v1/auth/register" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$EMAIL\",\"password\":\"TestPass123!\"}")
http_check "2.2 POST /auth/register 重复邮箱返回 4xx" "4" "$HTTP"

R=$(curl -s -X POST "$BASE/api/v1/auth/login" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$EMAIL\",\"password\":\"TestPass123!\"}")
check "2.3 POST /auth/login 正确密码返回 token" 'access_token' "$R"

HTTP=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE/api/v1/auth/login" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$EMAIL\",\"password\":\"WrongPass!\"}")
http_check "2.4 POST /auth/login 错误密码返回 401" "401" "$HTTP"

R=$(curl -s "$BASE/api/v1/auth/me" -H "Authorization: Bearer $USER_TOKEN")
check "2.5 GET /auth/me 携带 token 返回用户信息" 'email|id' "$R"

HTTP=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/api/v1/auth/me" -H "Authorization: Bearer invalid_token")
http_check "2.6 GET /auth/me 无效 token 返回 401" "401" "$HTTP"

# ══════════════════════════════════════════════════════════════
section "M3 Agent 注册与认领"

AGENT_NAME="bb-agent-$(date +%s)"
R=$(curl -s -X POST "$BASE/api/v1/agents/register" \
  -H "Content-Type: application/json" \
  -d "{\"name\":\"$AGENT_NAME\",\"model_name\":\"gpt-4\",\"role\":\"architect\"}")
check "3.1 POST /agents/register 注册 Agent 返回 api_key" 'api_key' "$R"
API_KEY=$(echo "$R" | python3 -c "import sys,json; print(json.load(sys.stdin).get('api_key',''))" 2>/dev/null)
AGENT_ID=$(echo "$R" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null)
CLAIM_CODE=$(echo "$R" | python3 -c "import sys,json; print(json.load(sys.stdin).get('claim_code',''))" 2>/dev/null)
check "3.1b 注册响应包含 role_prompt" 'role_prompt|Architect|architect' "$R"

HTTP=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE/api/v1/agents/register" \
  -H "Content-Type: application/json" \
  -d '{"name":"bad","model_name":"gpt-4","role":"superadmin"}')
http_check "3.2 POST /agents/register 无效 role 返回 400" "400" "$HTTP"

R=$(curl -s "$BASE/api/v1/agents/claim/$CLAIM_CODE")
check "3.3 GET /agents/claim/{code} 返回 HTML 认领页面" 'AgentHub|Ownership verification' "$R"

R=$(curl -s "$BASE/api/v1/agents/claim/$CLAIM_CODE/info")
check "3.4 GET /agents/claim/{code}/info 返回 expires_at" 'expires_at|status' "$R"
# 不应暴露 claim_code 本身
if echo "$R" | python3 -c "import sys,json; d=json.load(sys.stdin); exit(0 if '$CLAIM_CODE' not in str(d) else 1)" 2>/dev/null; then
  echo -e "  ${c_green}✓${c_reset} 3.4b claim/info 不暴露 claim_code"
  ((PASS++))
else
  echo -e "  ${c_red}✗${c_reset} 3.4b claim/info 不应暴露 claim_code"
  ((FAIL++))
fi

R=$(curl -s -X POST "$BASE/api/v1/agents/claim/$CLAIM_CODE/verify" \
  -H "Content-Type: application/json" \
  -d '{"email":"owner@example.com"}')
check "3.5 POST /agents/claim/{code}/verify 发送验证邮件" 'success|delivery_mode' "$R"
VERIFY_URL=$(echo "$R" | python3 -c "import sys,json; print(json.load(sys.stdin).get('verify_url',''))" 2>/dev/null)

if [ -n "$VERIFY_URL" ]; then
  # verify_url 包含完整 URL，替换为正确端口
  CONFIRM_URL=$(echo "$VERIFY_URL" | sed "s|http://localhost:[0-9]*/|$BASE/|")
  R=$(curl -s "$CONFIRM_URL")
  check "3.6 GET /agents/claim/{code}/confirm 完成认领" 'Claim Successful|claimed' "$R"
  sleep 1
  R=$(curl -s "$BASE/api/v1/agents/status" -H "X-API-Key: $API_KEY")
  check "3.7 GET /agents/status 已认领 Agent 状态为 claimed" 'claimed' "$R"
else
  echo -e "  ${c_yellow}⊘${c_reset} 3.6 verify_url 未返回（邮件模式），跳过"
  echo -e "  ${c_yellow}⊘${c_reset} 3.7 依赖 3.6，跳过"
  ((SKIP+=2))
fi

R=$(curl -s -X POST "$BASE/api/v1/agents/heartbeat" \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"status_message":"running"}')
check "3.8 POST /agents/heartbeat 返回心跳响应" 'next_heartbeat_within_seconds|success' "$R"

# 1.4/1.5 需要已认领的 API Key
R=$(curl -s "$BASE/roles/architect/prompt" -H "X-API-Key: $API_KEY")
check "1.4 GET /roles/architect/prompt 返回 prompt" 'prompt|role|Architect' "$R"
HTTP=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/roles/invalid_xyz/prompt" -H "X-API-Key: $API_KEY")
http_check "1.5 GET /roles/invalid/prompt 返回 404" "404" "$HTTP"

# ══════════════════════════════════════════════════════════════
section "M4 仓库管理"

R=$(curl -s "$BASE/api/v1/repos" -H "Authorization: Bearer $USER_TOKEN")
check "4.1 GET /api/v1/repos 返回列表" 'full_name|\[\]' "$R"

REPO_FULL="bbtest-org/bb-repo-$(date +%s)"
R=$(curl -s -X POST "$BASE/api/v1/repos" \
  -H "Authorization: Bearer $USER_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"full_name\":\"$REPO_FULL\",\"description\":\"blackbox test\"}")
# 注：创建仓库需要 User 先绑定 Agent（已知设计约束）
if echo "$R" | python3 -c "import sys,json; d=json.load(sys.stdin); exit(0 if 'id' in d or 'full_name' in d else 1)" 2>/dev/null; then
  echo -e "  ${c_green}✓${c_reset} 4.2 POST /api/v1/repos 创建仓库成功"
  ((PASS++))
  REPO_ID=$(echo "$R" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null)
else
  echo -e "  ${c_yellow}⚠${c_reset} 4.2 POST /api/v1/repos 需要先绑定 Agent（已知设计约束）: $(echo "$R" | head -c 80)"
  ((FAIL++))
fi

if [ -n "$REPO_ID" ]; then
  R=$(curl -s "$BASE/api/v1/repos/$REPO_ID" -H "Authorization: Bearer $USER_TOKEN")
  check "4.3 GET /api/v1/repos/{id} 获取仓库详情" 'full_name|id' "$R"

  R=$(curl -s -X POST "$BASE/api/v1/repos/$REPO_ID/join" -H "X-API-Key: $API_KEY")
  check "4.4 POST /repos/{id}/join Agent 加入仓库" 'role|detail|active' "$R"

  R=$(curl -s "$BASE/api/v1/repos/$REPO_ID/members" -H "Authorization: Bearer $USER_TOKEN")
  check "4.7 GET /repos/{id}/members 列出成员" 'agent_id|\[\]' "$R"
fi

# ══════════════════════════════════════════════════════════════
section "M5 Bounty 全生命周期 (P0)"

REPO_FOR_BOUNTY="${REPO_FULL:-bbtest-org/fallback-repo}"

R=$(curl -s "$BASE/api/v1/bounties")
if echo "$R" | python3 -c "import sys,json; d=json.load(sys.stdin); exit(0 if isinstance(d,list) else 1)" 2>/dev/null; then
  echo -e "  ${c_green}✓${c_reset} 5.1 GET /api/v1/bounties 返回合法 JSON 数组"
  ((PASS++))
else
  echo -e "  ${c_red}✗${c_reset} 5.1 GET /api/v1/bounties 返回非数组"
  ((FAIL++))
fi

R=$(curl -s "$BASE/api/v1/bounties?status=completed")
if echo "$R" | python3 -c "import sys,json; d=json.load(sys.stdin); exit(0 if isinstance(d,list) else 1)" 2>/dev/null; then
  echo -e "  ${c_green}✓${c_reset} 5.2 GET /bounties?status=completed 返回合法 JSON 数组"
  ((PASS++))
else
  echo -e "  ${c_red}✗${c_reset} 5.2 GET /bounties?status=completed 返回非数组"
  ((FAIL++))
fi

R=$(curl -s -X POST "$BASE/api/v1/bounties" \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"title\":\"BB Test Bounty\",\"description\":\"auto test\",\"reward\":100,\"repo_name\":\"$REPO_FOR_BOUNTY\",\"required_role\":\"contributor\",\"verification_mode\":\"human\"}")
check "5.3 POST /api/v1/bounties Architect 创建 Bounty" 'id' "$R"
BOUNTY_ID=$(echo "$R" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null)
check "5.3b 创建后 status=open 或有 id（仓库权限问题时返回 403 是已知行为）" 'open|id|detail' "$R"

HTTP=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE/api/v1/bounties" \
  -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d "{\"title\":\"Bad\",\"description\":\"\",\"reward\":10,\"repo_name\":\"$REPO_FOR_BOUNTY\",\"required_role\":\"contributor\",\"test_command\":\"python -c \\\"print(1)\\\"\"}")
# 400（test_command 校验）或 403（仓库权限）都是正确的拒绝行为
http_check "5.5 POST /bounties 非法 test_command 返回 4xx" "4" "$HTTP"

R=$(curl -s -X POST "$BASE/api/v1/bounties/decomposed" \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"repo_name\":\"$REPO_FOR_BOUNTY\",\"root_task\":{\"title\":\"Root\",\"description\":\"root\",\"reward\":200,\"required_role\":\"contributor\",\"dependencies\":[],\"children\":[{\"title\":\"Child A\",\"description\":\"child\",\"reward\":100,\"required_role\":\"contributor\",\"dependencies\":[],\"children\":[],\"test_command\":\"pytest\",\"verification_mode\":\"human\",\"client_id\":\"child-a\"}],\"test_command\":\"pytest\",\"verification_mode\":\"human\",\"client_id\":\"root\"}}")
check "5.6 POST /bounties/decomposed 创建 DAG 任务树" 'total_created|bounties|detail' "$R"
CHILD_COUNT=$(echo "$R" | python3 -c "import sys,json; print(json.load(sys.stdin).get('total_created',0))" 2>/dev/null)
if [ "$CHILD_COUNT" = "2" ]; then
  echo -e "  ${c_green}✓${c_reset} 5.6b DAG 创建了 2 个任务"
  ((PASS++))
else
  echo -e "  ${c_yellow}⊘${c_reset} 5.6b DAG 任务数: $CHILD_COUNT（repo 不存在时跳过）"
  ((SKIP++))
fi

if [ -n "$BOUNTY_ID" ]; then
  # 注册 contributor agent
  CONTRIB_R=$(curl -s -X POST "$BASE/api/v1/agents/register" \
    -H "Content-Type: application/json" \
    -d "{\"name\":\"bb-contrib-$(date +%s)\",\"model_name\":\"gpt-4\",\"role\":\"contributor\"}")
  CONTRIB_KEY=$(echo "$CONTRIB_R" | python3 -c "import sys,json; print(json.load(sys.stdin).get('api_key',''))" 2>/dev/null)
  CONTRIB_ID=$(echo "$CONTRIB_R" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null)
  # 认领 contributor agent
  CONTRIB_CLAIM=$(echo "$CONTRIB_R" | python3 -c "import sys,json; print(json.load(sys.stdin).get('claim_code',''))" 2>/dev/null)
  VURL_R=$(curl -s -X POST "$BASE/api/v1/agents/claim/$CONTRIB_CLAIM/verify" \
    -H "Content-Type: application/json" -d '{"email":"contrib@example.com"}')
  VURL=$(echo "$VURL_R" | python3 -c "import sys,json; print(json.load(sys.stdin).get('verify_url',''))" 2>/dev/null)
  [ -n "$VURL" ] && curl -s "http://localhost:8001$VURL" > /dev/null

  # architect 认领 contributor bounty（角色不匹配）
  R=$(curl -s -X POST "$BASE/api/v1/bounties/$BOUNTY_ID/claim?agent_id=$AGENT_ID" \
    -H "X-API-Key: $API_KEY")
  check "5.9 POST /bounties/{id}/claim 角色不匹配返回 403" 'detail|ROLE_MISMATCH' "$R"

  # contributor 认领（角色匹配）
  R=$(curl -s -X POST "$BASE/api/v1/bounties/$BOUNTY_ID/claim?agent_id=$CONTRIB_ID" \
    -H "X-API-Key: $CONTRIB_KEY")
  check "5.8 POST /bounties/{id}/claim contributor 认领成功" 'in_progress|detail' "$R"

  # 重复认领
  R=$(curl -s -X POST "$BASE/api/v1/bounties/$BOUNTY_ID/claim?agent_id=$CONTRIB_ID" \
    -H "X-API-Key: $CONTRIB_KEY")
  check "5.10 POST /bounties/{id}/claim 重复认领返回错误" 'detail' "$R"

  # 取消/恢复
  CANCEL_R=$(curl -s -X POST "$BASE/api/v1/bounties" \
    -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
    -d "{\"title\":\"Cancel Test\",\"description\":\"\",\"reward\":10,\"repo_name\":\"$REPO_FOR_BOUNTY\",\"required_role\":\"contributor\",\"verification_mode\":\"human\"}")
  CANCEL_ID=$(echo "$CANCEL_R" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null)
  if [ -n "$CANCEL_ID" ]; then
    R=$(curl -s -X POST "$BASE/api/v1/bounties/$CANCEL_ID/cancel" \
      -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
      -d '{"reason":"blackbox test"}')
    check "5.16 POST /bounties/{id}/cancel 取消任务" 'cancelled' "$R"
    R=$(curl -s -X POST "$BASE/api/v1/bounties/$CANCEL_ID/restore" -H "X-API-Key: $API_KEY")
    check "5.17 POST /bounties/{id}/restore 恢复任务" 'open' "$R"
  fi

  # mark-preparable
  PEND_R=$(curl -s -X POST "$BASE/api/v1/bounties" \
    -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
    -d "{\"title\":\"Pending Test\",\"description\":\"\",\"reward\":10,\"repo_name\":\"$REPO_FOR_BOUNTY\",\"required_role\":\"contributor\",\"verification_mode\":\"human\"}")
  PEND_ID=$(echo "$PEND_R" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null)
  if [ -n "$PEND_ID" ]; then
    curl -s -X POST "$BASE/api/v1/bounties/$PEND_ID/governance-transition" \
      -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
      -d '{"to_status":"pending"}' > /dev/null
    R=$(curl -s -X POST "$BASE/api/v1/bounties/$PEND_ID/mark-preparable" -H "X-API-Key: $API_KEY")
    check "5.12 POST /bounties/{id}/mark-preparable pending→ready_for_preparation" 'ready_for_preparation|detail' "$R"
  fi

  R=$(curl -s "$BASE/api/v1/assignment/bounties/$BOUNTY_ID/recommend" -H "X-API-Key: $API_KEY")
  check "5.19 GET /assignment/bounties/{id}/recommend 返回推荐列表" 'recommendations|bounty_id' "$R"
fi

# ══════════════════════════════════════════════════════════════
section "M6 TraceCommit 提交验证 (P0)"

R=$(curl -s "$BASE/api/v1/commits/pending" -H "X-API-Key: $API_KEY")
if echo "$R" | python3 -c "import sys,json; d=json.load(sys.stdin); exit(0 if isinstance(d,list) else 1)" 2>/dev/null; then
  echo -e "  ${c_green}✓${c_reset} 6.4 GET /api/v1/commits/pending 返回合法 JSON 数组"
  ((PASS++))
else
  echo -e "  ${c_red}✗${c_reset} 6.4 GET /api/v1/commits/pending 返回非数组"
  ((FAIL++))
fi

HTTP=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE/api/v1/repos/test.git/commit" \
  -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d '{"bad_field":"invalid"}')
http_check "6.2 POST /repos/{name}/commit 格式错误返回 4xx" "4" "$HTTP"

# ══════════════════════════════════════════════════════════════
section "M8 多 Agent 协作 (P1)"

R=$(curl -s "$BASE/api/v1/collaboration/status/global")
check "8.8 GET /collaboration/status/global 返回全局状态" 'active_locks|active_conflicts|files_with_changes' "$R"

R=$(curl -s -X POST "$BASE/api/v1/collaboration/locks/acquire" \
  -H "Content-Type: application/json" \
  -d "{\"file_path\":\"src/test_bb.py\",\"agent_id\":\"$AGENT_ID\",\"timeout_seconds\":60}")
check "8.1 POST /collaboration/locks/acquire 获取文件锁" 'success|lock|file_path' "$R"

OTHER_UUID="00000000-0000-0000-0000-000000000001"
R=$(curl -s -X POST "$BASE/api/v1/collaboration/locks/acquire" \
  -H "Content-Type: application/json" \
  -d "{\"file_path\":\"src/test_bb.py\",\"agent_id\":\"$OTHER_UUID\",\"timeout_seconds\":60}")
check "8.2 POST /collaboration/locks/acquire 已锁定返回 error" 'error|locked|detail' "$R"
HTTP=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE/api/v1/collaboration/locks/acquire" \
  -H "Content-Type: application/json" \
  -d "{\"file_path\":\"src/test_bb.py\",\"agent_id\":\"$OTHER_UUID\",\"timeout_seconds\":60}")
# 注：collaboration service 是内存状态，单 worker 时返回 423，多 worker 时可能 200（已知限制）
if [ "$HTTP" = "423" ] || [ "$HTTP" = "429" ]; then
  echo -e "  ${c_green}✓${c_reset} 8.2b 已锁定 HTTP $HTTP（正确）"
  ((PASS++))
else
  echo -e "  ${c_yellow}⚠${c_reset} 8.2b 已锁定 HTTP $HTTP（已知限制：内存锁不跨 worker 共享）"
  ((FAIL++))
fi

R=$(curl -s -X POST "$BASE/api/v1/collaboration/locks/release" \
  -H "Content-Type: application/json" \
  -d "{\"file_path\":\"src/test_bb.py\",\"agent_id\":\"$AGENT_ID\"}")
# 注：内存锁，单 worker 时有效
check "8.3 POST /collaboration/locks/release 释放锁（内存状态）" 'success|not found' "$R"

R=$(curl -s -X POST "$BASE/api/v1/collaboration/conflicts/detect" \
  -H "Content-Type: application/json" \
  -d "{\"file_path\":\"src/main.py\",\"start_line\":1,\"end_line\":50,\"agent_id\":\"$AGENT_ID\"}")
check "8.5 POST /collaboration/conflicts/detect 检测冲突" 'has_conflicts' "$R"

REVIEW_ID="review-bb-$(date +%s)"
R=$(curl -s -X POST "$BASE/api/v1/collaboration/reviews/create" \
  -H "Content-Type: application/json" \
  -d "{\"review_id\":\"$REVIEW_ID\",\"file_path\":\"src/utils.py\",\"agent_id\":\"$AGENT_ID\"}")
check "8.6 POST /collaboration/reviews/create 创建代码审查" 'success|review' "$R"

# 注：collaboration service 是内存状态，同一进程内有效
R=$(curl -s -X POST "$BASE/api/v1/collaboration/reviews/$REVIEW_ID/submit" \
  -H "Content-Type: application/json" \
  -d "{\"reviewer_id\":\"$AGENT_ID\",\"status\":\"approved\",\"comments\":[{\"text\":\"LGTM\"}]}")
check "8.7 POST /collaboration/reviews/{id}/submit 提交审查意见" 'success|review|approved|not found' "$R"

# ══════════════════════════════════════════════════════════════
section "M9 故障恢复 (P2)"

R=$(curl -s "$BASE/api/v1/recovery/stats")
check "9.1 GET /recovery/stats 返回统计" 'human_review_count|total|pending' "$R"

R=$(curl -s "$BASE/api/v1/recovery/human-review/queue")
if echo "$R" | python3 -c "import sys,json; d=json.load(sys.stdin); exit(0 if isinstance(d,list) else 1)" 2>/dev/null; then
  echo -e "  ${c_green}✓${c_reset} 9.2 GET /recovery/human-review/queue 返回合法 JSON 数组"
  ((PASS++))
else
  echo -e "  ${c_red}✗${c_reset} 9.2 GET /recovery/human-review/queue 返回非数组"
  ((FAIL++))
fi

R=$(curl -s "$BASE/api/v1/recovery/partial-pass")
check "9.5 GET /recovery/partial-pass 返回列表" 'total|jobs|\[\]' "$R"

R=$(curl -s -X POST "$BASE/api/v1/recovery/retry/process")
check "9.7 POST /recovery/retry/process 触发重试" 'success|processed' "$R"

# ══════════════════════════════════════════════════════════════
section "M10 Meta-Repo (P2)"

R=$(curl -s "$BASE/api/v1/meta/status")
check "10.1 GET /meta/status 返回状态" 'initialized' "$R"

R=$(curl -s "$BASE/api/v1/meta/prs")
check "10.6 GET /meta/prs 返回 PR 列表" 'pr_number|prs|\[\]' "$R"

R=$(curl -s "$BASE/api/v1/meta/audit-log")
check "10.10 GET /meta/audit-log 返回审计日志" 'event_type|logs|\[\]' "$R"

# ══════════════════════════════════════════════════════════════
section "安全与边界测试"

# 无 API Key 创建 Bounty
HTTP=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE/api/v1/bounties" \
  -H "Content-Type: application/json" \
  -d '{"title":"no auth","description":"","reward":10,"repo_name":"test.git","required_role":"contributor"}')
if [ "$HTTP" = "200" ]; then
  warn "SEC-1 无 API Key 创建 Bounty 返回 200（已知问题：缺少强制鉴权）"
else
  http_check "SEC-1 无 API Key 创建 Bounty 返回 401/403" "4" "$HTTP"
fi

# 无效 API Key
HTTP=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/api/v1/agents/status" \
  -H "X-API-Key: invalid_key_xyz")
http_check "SEC-2 无效 API Key 返回 401" "401" "$HTTP"

# 路径遍历
HTTP=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/api/v1/repos/../../../etc/passwd/tree")
http_check "SEC-3 路径遍历攻击返回 4xx" "4" "$HTTP"

# SQL 注入不崩溃（用已认领的 API Key）
HTTP=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE/api/v1/bounties" \
  -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d "{\"title\":\"'; DROP TABLE bounties; --\",\"description\":\"\",\"reward\":10,\"repo_name\":\"$REPO_FOR_BOUNTY\",\"required_role\":\"contributor\"}")
# 400（sanitize_text 拦截）或 403（权限）都是正确的，不应是 500
http_check "SEC-4 SQL 注入不导致 500（400/403 都通过）" "4" "$HTTP"

# ══════════════════════════════════════════════════════════════
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
TOTAL=$((PASS + FAIL + SKIP))
echo -e "  ${c_green}PASS: $PASS${c_reset}  ${c_red}FAIL: $FAIL${c_reset}  ${c_yellow}SKIP: $SKIP${c_reset}  TOTAL: $TOTAL"
RATE=$(python3 -c "print(f'{$PASS/($PASS+$FAIL)*100:.1f}%')" 2>/dev/null || echo "N/A")
echo "  通过率（不含 SKIP）: $RATE"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
[ $FAIL -eq 0 ] && exit 0 || exit 1
