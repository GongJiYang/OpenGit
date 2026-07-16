#!/usr/bin/env bash
# Quick smoke test for role collaboration flow in dev bypass mode.
# Requires: bash dev.sh running on port 8000

BASE="http://localhost:8000"
ARCH_H='-H "X-Dev-Role: architect"'
CONTRIB_H='-H "X-Dev-Role: contributor"'

echo "=== 1. Health check ==="
curl -s "$BASE/" | python3 -m json.tool

echo ""
echo "=== 2. Architect creates a Bounty ==="
BOUNTY=$(curl -s -X POST "$BASE/api/v1/bounties" \
  -H "Content-Type: application/json" \
  -H "X-Dev-Role: architect" \
  -d '{
    "title": "Implement login API",
    "description": "Build POST /auth/login endpoint",
    "reward": 100,
    "repo_name": "dev-org/test-repo",
    "required_role": "contributor",
    "verification_mode": "human"
  }')
echo "$BOUNTY" | python3 -m json.tool
BOUNTY_ID=$(echo "$BOUNTY" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])" 2>/dev/null)
echo "Bounty ID: $BOUNTY_ID"

echo ""
echo "=== 3. List open bounties ==="
curl -s "$BASE/api/v1/bounties" | python3 -m json.tool

echo ""
echo "=== 4. Contributor claims the bounty ==="
# Get contributor's dev agent id
CONTRIB_ID=$(curl -s "$BASE/api/v1/agents/status" \
  -H "X-Dev-Role: contributor" 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('id',''))" 2>/dev/null || echo "")

if [ -n "$BOUNTY_ID" ]; then
  # Use architect's id from the bypass principal — in dev mode agent_id check is relaxed
  ARCH_ID=$(echo "$BOUNTY" | python3 -c "import sys,json; print(json.load(sys.stdin).get('assignee','dev-agent'))" 2>/dev/null || echo "dev-agent")
  curl -s -X POST "$BASE/api/v1/bounties/$BOUNTY_ID/claim?agent_id=dev-contributor-id" \
    -H "X-Dev-Role: contributor" | python3 -m json.tool
fi

echo ""
echo "=== Done. Check http://localhost:8000/docs for full API ==="
