# ruff: noqa: E402
import sys
import os
import logging
import subprocess
import tempfile
import shutil
from typing import Any, List, Optional
from fastapi import Body, Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel as _BaseModel  # noqa: F401
# from agent_auth.services.workitem_service import WorkItemService  # imported where used
from sqlmodel import Session, select
from core.middleware import limiter, setup_rate_limit_and_middlewares
from core.settings import get_settings

# --- Hack for Monorepo Paths (MVP only) ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)
sys.path.append(os.path.abspath(os.path.join(BASE_DIR, "../../../packages/protocol/src")))
sys.path.append(os.path.abspath(os.path.join(BASE_DIR, "../../../services/git-core/src")))
sys.path.append(os.path.abspath(os.path.join(BASE_DIR, "../../../services/semantic-store/src")))
sys.path.append(os.path.abspath(os.path.join(BASE_DIR, "../../../services/execution-vmm/src")))
# Add monorepo root to import skills/bots
sys.path.append(os.path.abspath(os.path.join(BASE_DIR, "../../../")))

from core.lifespan import lifespan
from dependencies.services import get_indexer, get_sandbox
from core.security import get_secure_repo_path
from agent_auth.routers import agent_router, claim_router, wechat_router
from meta import meta_router
from agent_auth.deps import get_auth_session
# get_auth_engine removed from public surface; use app-level engine if needed
# from agent_auth.models import Agent, AgentStatus  # internal; avoid direct use
# from agent_auth.utils import get_api_key_prefix, get_legacy_api_key_prefix, is_valid_api_key_format  # internal; avoid direct use
# from agent_auth.validators import get_validator  # avoid internal import; TODO: expose via facade if needed
from dependencies.auth import require_agent
from persistence import Bounty, PlatformPR, get_session
from routers.bounties import router as bounties_router
from routers.commits import router as commits_router
from routers.leaderboard import router as leaderboard_router
from routers.repos import router as repos_router
from routers.system import router as system_router
from schemas.repos import CreateRepoRequest
from schemas.search import SearchResponse
from schemas.workitems import WorkItemListResponse
# from agent_auth.services.memory_service import memory_service  # TODO: expose via facade if external usage required

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


ALLOWED_TEST_COMMANDS = ["pytest", "python", "python3", "tox", "nose"]

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
    raw: bool = False,
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

    if raw:
        return FileResponse(prompt_path, media_type="text/markdown")

    with open(prompt_path, "r", encoding="utf-8") as f:
        prompt = f.read()

    # Inject Historical Memories if agent_id is provided
    if agent_id:
        # TODO: use memory facade; temporary no-op until exposed
        memories = []
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


@app.get("/api/v1/repos")
@app.get("/repos")
@limiter.limit("30/minute")
def list_repos(request: Request):
    if not os.path.exists(request.app.state.store_root):
        return []
    return [d for d in os.listdir(request.app.state.store_root) if not d.startswith('.')]


@app.post("/api/v1/repos")
@app.post("/repos")
@limiter.limit("10/minute")
def create_repo(request: Request, req: CreateRepoRequest, agent: Any = Depends(require_agent)):
    """Creates a new AgentHub repository with Protocol Hooks."""
    # Security validation
    get_secure_repo_path(req.name)
    try:
        path = request.app.state.repo_manager.create_repo(req.name)
        return {"id": req.name, "path": path, "status": "created"}
    except Exception as e:
        # Avoid leaking internal error details to clients
        logger.error("[create_repo] error: %s: %s", type(e).__name__, e)
        raise HTTPException(status_code=500, detail="Failed to create repository")


@app.post("/api/v1/index")
@app.post("/index")
def index_code(
    request: Request,
    repo_name: str,
    file_path: str,
    content: str = Body(..., media_type="text/plain"),
    agent: Any = Depends(require_agent),
):
    """
    Manually index code content.
    """
    get_secure_repo_path(repo_name)
    parser = getattr(request.app.state, "parser", None)
    idx = get_indexer(request)
    if not parser or not idx:
        return {"indexed_chunks": 0}
    chunks = parser.parse(content)
    for c in chunks:
        idx.index_chunk(repo_name, file_path, c)
    return {"indexed_chunks": len(chunks)}


@app.get("/api/v1/search", response_model=List[SearchResponse])
@app.get("/search", response_model=List[SearchResponse])
def search_code(request: Request, query: str, repo_id: Optional[str] = None, limit: int = 3, offset: int = 0):
    """Semantic search for code chunks."""
    if limit < 1:
        raise HTTPException(status_code=400, detail="limit must be >= 1")
    if limit > 20:
        limit = 20
    if offset < 0:
        raise HTTPException(status_code=400, detail="offset must be >= 0")

    fetch_limit = min(limit + offset, 50)
    idx = get_indexer(request)
    if not idx:
        return []
    results = idx.search(query, limit=fetch_limit, repo_id=repo_id)
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
    # Simple whitelist check for the base command
    # Ensures only authorized test runners are executed
    base_cmd = cmd.split()[0] if cmd else ""
    if base_cmd not in ALLOWED_TEST_COMMANDS:
        raise HTTPException(
            status_code=400,
            detail=f"Command '{base_cmd}' is not allowed. Supported: {ALLOWED_TEST_COMMANDS}",
        )

    bare_repo_path = get_secure_repo_path(repo_name)
    sb = get_sandbox(request)
    if not sb:
        raise HTTPException(status_code=503, detail="Sandbox is disabled")

    # Clone bare repo to a temporary working directory so tests have a worktree
    work_dir = tempfile.mkdtemp(prefix="agenthub_verify_")
    try:
        subprocess.run(["git", "clone", bare_repo_path, work_dir], check=True, capture_output=True)
        exit_code, output = sb.run_tests(work_dir, cmd)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    return {
        "repo": repo_name,
        "exit_code": exit_code,
        "passed": exit_code == 0,
        "logs": output[:1000],
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

    return app


# Backward-compatible module-level app
app = create_app()
