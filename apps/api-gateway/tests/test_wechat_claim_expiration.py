import asyncio
from datetime import datetime, timedelta

from agent_auth.models import AgentStatus
from agent_auth.strategies import WeChatClaimStrategy


class _FakeAgent:
    def __init__(self, *, agent_id: str, name: str, status: AgentStatus, claim_expires_at: datetime):
        self.id = agent_id
        self.name = name
        self.status = status
        self.claim_expires_at = claim_expires_at


class _FakeRepo:
    def __init__(self, agent):
        self.agent = agent
        self.update_calls = []

    def get_by_claim_code(self, claim_code: str):
        return self.agent

    def update_status_and_owner(self, agent_id: str, status: AgentStatus, owner_openid: str):
        self.update_calls.append((agent_id, status, owner_openid))
        self.agent.status = status
        self.agent.owner_wechat_openid = owner_openid
        return self.agent


def test_wechat_claim_rejects_expired_claim_code():
    agent = _FakeAgent(
        agent_id="agent-1",
        name="wechat-agent",
        status=AgentStatus.PENDING,
        claim_expires_at=datetime.utcnow() - timedelta(minutes=1),
    )
    repo = _FakeRepo(agent)
    strategy = WeChatClaimStrategy(repo)

    result = asyncio.run(strategy.execute_claim("EXPIRED01", "openid-1"))

    assert result.success is False
    assert "过期" in result.message
    assert repo.update_calls == []


def test_wechat_claim_succeeds_when_claim_code_not_expired():
    agent = _FakeAgent(
        agent_id="agent-2",
        name="wechat-agent-ok",
        status=AgentStatus.PENDING,
        claim_expires_at=datetime.utcnow() + timedelta(minutes=10),
    )
    repo = _FakeRepo(agent)
    strategy = WeChatClaimStrategy(repo)

    result = asyncio.run(strategy.execute_claim("VALID001", "openid-2"))

    assert result.success is True
    assert repo.update_calls == [("agent-2", AgentStatus.CLAIMED, "openid-2")]
