# ruff: noqa: E402
import os
import logging
import subprocess
import tempfile
import shutil
from uuid import UUID
from typing import Any, List, Optional
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel as _BaseModel  # noqa: F401
# from agent_auth.services.workitem_service import WorkItemService  # imported where used
from agenthub_execution_vmm.guard import ExecutionGuard
from sqlmodel import Session, select

from agent_auth.models import Agent
from agent_auth.models.platform import UserAgentBinding
from core.middleware import limiter, setup_rate_limit_and_middlewares
from core.settings import get_settings

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

from core.lifespan import lifespan
from dependencies.services import get_indexer, get_sandbox, get_session_manager
from core.security import ensure_governance_allows_execution, get_secure_repo_path
from agent_auth.routers import agent_router, claim_router, oauth_router, wechat_router
from meta import meta_router
from agent_auth.deps import get_auth_session
from agent_auth.services.memory_service import get_memory_service
# get_auth_engine removed from public surface; use app-level engine if needed
# from agent_auth.models import Agent, AgentStatus  # internal; avoid direct use
# from agent_auth.utils import get_api_key_prefix, get_legacy_api_key_prefix, is_valid_api_key_format  # internal; avoid direct use
# from agent_auth.validators import get_validator  # avoid internal import; TODO: expose via facade if needed
from dependencies.auth import require_active_identity, require_agent
from persistence import Bounty, PlatformPR, get_session
from routers.bounties import router as bounties_router
from routers.commits import router as commits_router
from routers.leaderboard import router as leaderboard_router
from routers.repos import router as repos_router
from routers.system import router as system_router
from routers.backlog_governance import router as backlog_governance_router
from schemas.search import SearchResponse
from schemas.workitems import WorkItemListResponse

logger = logging.getLogger(__name__)
# ... (rest of imports)

# Static/CORS paths
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
PROMPT_DIR = os.path.join(os.path.dirname(__file__), "prompts")

# Defer app creation until after lifespan is defined below

# Create app early so route decorators can bind
app = FastAPI(title="AgentHub API", version="0.1.0", lifespan=lifespan)

# --- Models ---


# Bounty model is now imported from persistence.py


# --- Routes ---


@app.get("/")
@limiter.limit("60/minute")
def read_root(request: Request):
    return {
        "status": "online",
        "system": "AgentHub V2",
        "for_ai_agents": "Visit /agent.md for complete instructions",
        "quickstart": "curl -s https://api.agenthub.dev/agent.md",
    }


@app.get("/agent.md")
async def get_agent_guide():
    """AI-readable instruction manual."""
    agent_md_path = os.path.join(STATIC_DIR, "agent.md")
    if os.path.exists(agent_md_path):
        return FileResponse(agent_md_path, media_type="text/markdown")
    return {"error": "Agent guide not found"}


@app.get("/skill.md")
async def get_skill_guide():
    """AI-readable skill guide."""
    skill_md_path = os.path.join(STATIC_DIR, "skill.md")
    if os.path.exists(skill_md_path):
        return FileResponse(skill_md_path, media_type="text/markdown")
    return {"error": "Skill guide not found"}


@app.get("/heartbeat.md")
async def get_heartbeat_guide():
    """AI-readable heartbeat guide."""
    heartbeat_md_path = os.path.join(STATIC_DIR, "heartbeat.md")
    if os.path.exists(heartbeat_md_path):
        return FileResponse(heartbeat_md_path, media_type="text/markdown")
    return {"error": "Heartbeat guide not found"}


@app.get("/rules.md")
async def get_rules_guide():
    """AI-readable rules guide."""
    rules_md_path = os.path.join(STATIC_DIR, "rules.md")
    if os.path.exists(rules_md_path):
        return FileResponse(rules_md_path, media_type="text/markdown")
    return {"error": "Rules guide not found"}


@app.get("/roles/{role_name}/prompt")
async def get_role_prompt(
    role_name: str,
    agent_id: Optional[str] = None,
    query: Optional[str] = None,
    memory_scope: str = "private",
    raw: bool = False,
    principal: Any = Depends(require_active_identity),
    auth_session: Session = Depends(get_auth_session),
):
    """Return the system prompt for a given role, optionally injecting agent memories."""
    role = role_name.lower().strip()
    prompt_map = {
        "architect": "architect.md",
        "contributor": "contributor.md",
        "reviewer": "reviewer.md",
        "executor": "executor.md",
        "librarian": "librarian.md",
        "observer": "librarian.md",
        "tester": "tester.md",
    }
    filename = prompt_map.get(role)
    if not filename:
        raise HTTPException(status_code=404, detail="Role prompt not found")
    prompt_path = os.path.join(PROMPT_DIR, filename)
    if not os.path.exists(prompt_path):
        raise HTTPException(status_code=404, detail="Role prompt not found")

    principal_id = str(getattr(principal, "id", ""))
    principal_kind = getattr(principal, "kind", None)

    target_agent_id = agent_id
    if target_agent_id is None and principal_kind == "agent":
        target_agent_id = principal_id

    if target_agent_id:
        if principal_kind == "agent":
            if principal_id != target_agent_id:
                raise HTTPException(status_code=403, detail="Forbidden: cannot access other agent memories")
        else:
            target_agent: Optional[Agent] = None
            try:
                target_agent = auth_session.get(Agent, UUID(target_agent_id))
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid agent_id")

            if not target_agent:
                raise HTTPException(status_code=404, detail="Agent not found")

            binding = auth_session.exec(
                select(UserAgentBinding).where(UserAgentBinding.agent_id == target_agent.id)
            ).first()
            owner_user_id = str(binding.user_id) if binding else None

            if owner_user_id != principal_id:
                raise HTTPException(status_code=403, detail="Forbidden: cannot access other agent memories")

    if raw:
        return FileResponse(prompt_path, media_type="text/markdown")

    with open(prompt_path, "r", encoding="utf-8") as f:
        prompt = f.read()

    if target_agent_id:
        memories = get_memory_service().get_memories(
            target_agent_id,
            query=query,
            role=role,
            scope=memory_scope,
        )
        if memories:
            memory_context = "\n\n### 🧠 RELEVANT HISTORICAL EXPERIENCE\n"
            for i, mem in enumerate(memories):
                content = mem.get("content", mem.get("text", ""))
                memory_context += f"{i+1}. {content}\n"
            prompt += memory_context

    return {"role": role, "prompt": prompt}


# --- Agents List (Public View) ---


@app.get("/api/v1/agents")
@app.get("/agents")
@limiter.limit("30/minute")
def list_agents(request: Request, auth_session: Session = Depends(get_auth_session)):
    """
    List all registered agents (public view).

    Returns agent info without sensitive data like API keys.
    """
    # TODO: expose agent listing via facade; return empty until available
    return []


@app.post("/api/v1/index")
@app.post("/index")
def index_code(
    request: Request,
    repo_name: str,
    file_path: str,
    agent: Any = Depends(require_agent),
):
    """
    Index repository file content from HEAD only.
    """
    from core.security import validate_blob_path

    repo_path = get_secure_repo_path(repo_name)
    validate_blob_path(file_path)

    parser = getattr(request.app.state, "parser", None)
    idx = get_indexer(request)
    if not parser or not idx:
        return {"indexed_chunks": 0}

    if not os.path.exists(repo_path):
        raise HTTPException(status_code=404, detail="Repo not found")

    try:
        content = subprocess.check_output(
            ["git", "show", f"HEAD:{file_path}"],
            cwd=repo_path,
            stderr=subprocess.PIPE,
        ).decode()
    except subprocess.CalledProcessError:
        raise HTTPException(status_code=404, detail="File not found in repository HEAD")

    chunks = parser.parse(content, file_path=file_path)
    idx.clear_file_index(repo_name, file_path)
    for c in chunks:
        idx.index_chunk(repo_name, file_path, c)
    return {"indexed_chunks": len(chunks)}


@app.get("/api/v1/search", response_model=List[SearchResponse])
@app.get("/search", response_model=List[SearchResponse])
def search_code(
    request: Request,
    query: str,
    repo_name: Optional[str] = None,
    limit: int = 3,
    offset: int = 0,
    strict: bool = False,
    principal: Any = Depends(require_active_identity),
):
    """Semantic search for code chunks."""
    if limit < 1:
        raise HTTPException(status_code=400, detail="limit must be >= 1")
    if limit > 20:
        limit = 20
    if offset < 0:
        raise HTTPException(status_code=400, detail="offset must be >= 0")

    _ = principal
    fetch_limit = min(limit + offset, 50)
    idx = get_indexer(request)
    if not idx:
        if strict:
            raise HTTPException(status_code=503, detail="Semantic search is disabled")
        return []
    results = idx.search(query, limit=fetch_limit, repo_name=repo_name)
    status_getter = getattr(idx, "get_last_search_status", None)
    search_status = status_getter() if callable(status_getter) else {"ok": True, "unavailable": False}
    if search_status.get("unavailable"):
        if strict:
            detail = {
                "message": "Semantic search backend unavailable",
                "error_code": search_status.get("error_code"),
                "reason": search_status.get("reason"),
            }
            raise HTTPException(status_code=503, detail=detail)
        return []
    results = results[offset : offset + limit] if offset else results[:limit]
    response = []
    for r in results:
        payload = r.get("payload", {}) if isinstance(r, dict) else {}
        response.append(
            SearchResponse(
                chunk_name=payload.get("chunk_name", ""),
                code_snippet=payload.get("code_snippet", ""),
                score=r.get("score", 0.0) if isinstance(r, dict) else 0.0,
            )
        )
    return response


@app.post("/api/v1/verify")
@app.post("/verify")
@limiter.limit("10/minute")
def verify_repo(request: Request, repo_name: str, cmd: str = "pytest", agent: Any = Depends(require_agent)):
    """Trigger the Sandbox to run tests on a repo (with command validation)."""
    ensure_governance_allows_execution(verify_endpoint=True)

    normalized_cmd = (cmd or "pytest").strip()
    try:
        tokens = ExecutionGuard.verify_command(normalized_cmd)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))

    bare_repo_path = get_secure_repo_path(repo_name)
    settings = get_settings()
    sandbox_provider = settings.normalized_sandbox_provider
    if sandbox_provider == "subprocess" and settings.normalized_security_mode == "strict":
        raise HTTPException(status_code=503, detail="Subprocess sandbox is not allowed in strict security mode")
    if sandbox_provider == "subprocess" and not settings.app_allow_insecure_subprocess_sandbox:
        raise HTTPException(
            status_code=503,
            detail="Subprocess sandbox requires APP_ALLOW_INSECURE_SUBPROCESS_SANDBOX=true in warn mode",
        )
    if sandbox_provider == "runner":
        raise HTTPException(
            status_code=409,
            detail="Local verify endpoint is unavailable when APP_SANDBOX_PROVIDER=runner",
        )
    if sandbox_provider != "subprocess":
        raise HTTPException(status_code=503, detail="Sandbox is disabled")

    sb = get_sandbox(request)
    if not sb:
        raise HTTPException(status_code=503, detail="Subprocess sandbox is not initialized")

    session_manager = get_session_manager(request)
    if not session_manager:
        raise HTTPException(status_code=503, detail="Session manager is not initialized")

    # Clone bare repo to a temporary working directory so tests have a worktree
    work_dir = tempfile.mkdtemp(prefix="agenthub_verify_")
    verify_task_id = f"verify:{repo_name}"
    try:
        subprocess.run(["git", "clone", bare_repo_path, work_dir], check=True, capture_output=True)
        session_manager.get_or_create_session(str(agent.id), verify_task_id, work_dir)
        exit_code, output = session_manager.execute_with_status(str(agent.id), verify_task_id, " ".join(tokens))
    finally:
        if session_manager:
            session_manager.close_session(str(agent.id), verify_task_id)
        shutil.rmtree(work_dir, ignore_errors=True)

    return {
        "repo": repo_name,
        "exit_code": exit_code,
        "passed": exit_code == 0,
        "logs": ExecutionGuard.sanitize_output(output, max_length=1000),
    }


# --- Bounty Board (Job Market) ---


@app.get("/api/v1/workitems")
@limiter.limit("60/minute")
def list_workitems(
    request: Request,
    kind: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
    session: Session = Depends(get_session),
):
    """聚合工作项：bounty + meta_pr（后续可扩展 update）。

    Query:
    - kind: bounty|meta_pr（为空则两者都返回）
    - status: 过滤状态（各自枚举的原始值）
    - limit: 数量上限
    """
    from agent_auth.services.workitem_service import WorkItemService

    wis = WorkItemService(session)
    items = []

    def push_bounty(b: Bounty):
        items.append(
            {
                "kind": "bounty",
                "id": b.id,
                "title": b.title,
                "status": b.status,
                "repo_name": b.repo_name,
                "assignee": b.assignee,
            }
        )

    def push_pr(pr: PlatformPR):
        items.append(
            {
                "kind": "meta_pr",
                "id": pr.pr_number,
                "title": pr.title,
                "status": pr.status,
                "author_type": pr.author_type,
                "author_id": pr.author_id,
            }
        )

    # bounty
    if not kind or kind == "bounty":
        stmt = select(Bounty).order_by(Bounty.created_at.desc()).limit(limit)
        if status:
            stmt = stmt.where(Bounty.status == status)
        for b in session.exec(stmt).all():
            push_bounty(b)

    # meta_pr
    if not kind or kind == "meta_pr":
        prs = wis.meta.list_prs(status_filter=status, limit=limit)
        for pr in prs:
            push_pr(pr)

    # 截断合并后的数量
    if len(items) > limit:
        items = items[:limit]

    return WorkItemListResponse(items=items)


# --- App Factory ---


def create_app() -> FastAPI:
    # Use the already-created global app (routes are bound via decorators above)
    global app

    # rate limit + middleware
    setup_rate_limit_and_middlewares(app)

    # static
    if os.path.exists(STATIC_DIR):
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    # CORS
    frontend_url = get_settings().frontend_url
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[frontend_url],
        allow_origin_regex=r"http://localhost:.*",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # routers
    app.include_router(agent_router)
    app.include_router(claim_router)
    app.include_router(oauth_router)
    app.include_router(wechat_router)
    app.include_router(meta_router)
    app.include_router(bounties_router)
    app.include_router(commits_router)
    app.include_router(repos_router)
    app.include_router(system_router)
    app.include_router(leaderboard_router)

    from agent_auth.routers.assignment import router as assignment_router

    app.include_router(assignment_router, prefix="/api/v1")

    from agent_auth.routers.collaboration import router as collaboration_router

    app.include_router(collaboration_router, prefix="/api/v1")

    from agent_auth.routers.recovery import router as recovery_router

    app.include_router(recovery_router, prefix="/api/v1")

    from agent_auth.routers.platform import platform_router

    app.include_router(platform_router, prefix="/api/v1")

    from agent_auth.routers.runner import router as runner_router

    app.include_router(runner_router, prefix="/api/v1")

    # Skills router (M3-4): start & job polling endpoints
    from skills.api_router import router as skills_router  # type: ignore

    app.include_router(skills_router, prefix="/api/v1")
    app.include_router(backlog_governance_router, prefix="/api/v1")

    return app


# Backward-compatible module-level app
app = create_app()
