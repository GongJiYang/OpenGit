# ruff: noqa: E402
import sys
import os
import re
import subprocess
import tempfile
import shutil
import json
import html
from typing import List, Optional, Any, Tuple, Set
from fastapi import FastAPI, HTTPException, Body, Depends, Request, Header, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ConfigDict
from agent_auth.models.platform import RepoRole
from pydantic import BaseModel as _BaseModel  # noqa: F401
from sqlmodel import Session, select
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

# --- Hack for Monorepo Paths (MVP only) ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)
sys.path.append(os.path.abspath(os.path.join(BASE_DIR, "../../../packages/protocol/src")))
sys.path.append(os.path.abspath(os.path.join(BASE_DIR, "../../../services/git-core/src")))
sys.path.append(os.path.abspath(os.path.join(BASE_DIR, "../../../services/semantic-store/src")))
sys.path.append(os.path.abspath(os.path.join(BASE_DIR, "../../../services/execution-vmm/src")))

import asyncio
from agenthub_git_core.repo_manager import RepoManager
from agenthub_semantic_store.indexer import VectorIndexer
from agenthub_semantic_store.ast_parser import PythonASTParser
from agenthub_execution_vmm.sandbox import SubprocessSandbox
from agenthub_execution_vmm.guard import ExecutionGuard
from agenthub_protocol.path_utils import ensure_safe_path
from agenthub_protocol.validator import TraceValidator
from agent_auth import agent_router, claim_router, wechat_router
from meta import meta_router
from agent_auth.database import get_db as get_auth_session, get_engine as get_auth_engine
from agent_auth.models import Agent, AgentStatus
from agent_auth.services import start_scheduler, stop_scheduler
from agent_auth.utils import verify_api_key, get_api_key_prefix, get_legacy_api_key_prefix, is_valid_api_key_format
from agent_auth.validators import get_validator
from agent_auth.services.penalty_service import PenaltyService
from agent_auth.services.user_auth import UserAuthService
from persistence import Bounty, CommitRecord, get_session
from git_tree_service import GitTreeService
from agent_auth.services.memory_service import memory_service
from contextlib import asynccontextmanager
# --- Execution & Cost Guards ---
# [Blind-Spot 2] Global Concurrency Limit
MAX_CONCURRENT_RUNS = int(os.getenv("MAX_CONCURRENT_RUNS", "3"))
execution_semaphore = asyncio.Semaphore(MAX_CONCURRENT_RUNS)

from datetime import date, datetime
import time
import logging
logger = logging.getLogger(__name__)

class DailyBudgetTracker:
    """Simple JSON-based daily budget tracker."""
    def __init__(self, limit: float = 10.0):
        self.limit = limit
        self.path = os.path.abspath("./agenthub_data/daily_budget.json")
        self.lock_path = os.path.abspath("./agenthub_data/daily_budget.lock")
        self._ensure_file()

    def _ensure_file(self):
        if not os.path.exists(self.path):
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w") as f:
                json.dump({"date": str(date.today()), "spent": 0.0}, f)

    def check_and_record(self, amount: float) -> bool:
        lockf = None
        try:
            today_str = str(date.today())
            os.makedirs(os.path.dirname(self.lock_path), exist_ok=True)
            lockf = open(self.lock_path, "w")
            try:
                import fcntl
                fcntl.flock(lockf, fcntl.LOCK_EX)
            except Exception:
                pass

            with open(self.path, "r") as f:
                data = json.load(f)

            if data.get("date") != today_str:
                data = {"date": today_str, "spent": 0.0}

            data["spent"] += amount
            if data["spent"] > self.limit:
                return False

            with open(self.path, "w") as f:
                json.dump(data, f)
            return True
        except Exception:
            # Fail-closed: if budget file ops fail, deny execution to preserve cost guard
            return False
        finally:
            if lockf:
                try:
                    import fcntl
                    fcntl.flock(lockf, fcntl.LOCK_UN)
                except Exception:
                    pass
                try:
                    lockf.close()
                except Exception:
                    pass

budget_tracker = DailyBudgetTracker(limit=10.0)
# ... (rest of imports)

# --- Rate Limiting ---
limiter = Limiter(key_func=get_remote_address)

# --- Request Size Limit (20MB) ---
MAX_REQUEST_SIZE = 20 * 1024 * 1024  # 20MB

# Define middleware function; bind to app after creation
async def limit_request_size_mw(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > MAX_REQUEST_SIZE:
        return JSONResponse(
            status_code=413,
            content={"detail": f"Payload too large. Maximum allowed size is {MAX_REQUEST_SIZE / 1024 / 1024}MB."}
        )
    return await call_next(request)

# Static/CORS paths
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
PROMPT_DIR = os.path.join(os.path.dirname(__file__), "prompts")

# Defer app creation until after lifespan is defined below

# --- Services Singleton ---
STORE_ROOT = os.path.abspath("./agenthub_data/repos")

def get_repo_manager(request: Request) -> RepoManager:
    mgr = getattr(request.app.state, "repo_manager", None)
    if not mgr:
        raise HTTPException(status_code=500, detail="RepoManager not initialized")
    return mgr

def get_indexer(request: Request) -> Optional[VectorIndexer]:
    return getattr(request.app.state, "indexer", None)

def get_sandbox(request: Request) -> Optional[SubprocessSandbox]:
    return getattr(request.app.state, "sandbox", None)

def get_secure_repo_path(repo_name: str) -> str:
    """Ensures repo_name stays within STORE_ROOT."""
    try:
        return str(ensure_safe_path(STORE_ROOT, repo_name, "Invalid repository name"))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

def validate_blob_path(path: str):
    """Simple check to prevent escaping git tree structure via path parameter."""
    # Since we don't have a 'base' directory for the git tree yet here
    # (it's internal to git), we still use the basic check,
    # but we can also use ensure_safe_path with a dummy base if needed.
    # However, for git blobs, the path is relative to the repo root.
    if ".." in path or path.startswith("/"):
        raise HTTPException(status_code=400, detail="Invalid file path")
    if any(ch in path for ch in [":", "\\", "\x00"]) or path.startswith("-"):
        raise HTTPException(status_code=400, detail="Invalid file path")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 读取开关（默认关闭，测试安全）
    enable_indexer = os.getenv("APP_ENABLE_INDEXER", "0") == "1"
    enable_sandbox = os.getenv("APP_ENABLE_SANDBOX", "0") == "1"

    # 统一初始化到 app.state
    app.state.store_root = os.path.abspath("./agenthub_data/repos")
    os.makedirs(app.state.store_root, exist_ok=True)

    app.state.repo_manager = RepoManager(app.state.store_root)

    app.state.indexer = None
    if enable_indexer:
        idx = VectorIndexer(collection_name="agenthub_prod", embedding_dim=1024)
        if not getattr(idx.embedder, "client", None):
            # 缺密钥时不启
            idx = None
        app.state.indexer = idx

    app.state.parser = PythonASTParser()

    app.state.sandbox = SubprocessSandbox() if enable_sandbox else None

    # 可选：按需启动 scheduler（多 pod 场景建议加开关 RUN_SCHEDULER=1）
    if os.getenv("RUN_SCHEDULER") == "1":
        from sqlmodel import Session as AuthSession
        def session_factory():
            return AuthSession(get_auth_engine())
        start_scheduler(session_factory)

    try:
        yield
    finally:
        # 关闭 sandbox / scheduler
        if os.getenv("RUN_SCHEDULER") == "1":
            stop_scheduler()
        # 如 indexer/sandbox 有 close()，在此清理

# Create app early so route decorators can bind
app = FastAPI(title="AgentHub API", version="0.1.0", lifespan=lifespan)

# --- Models ---

class AgentIdentity(BaseModel):
    agent_id: str
    model_name: str

# Bounty model is now imported from persistence.py

class CreateRepoRequest(BaseModel):
    name: str

class CreateBountyRequest(BaseModel):
    # Strict: forbid unknown fields
    model_config = ConfigDict(extra="forbid")

    title: str
    description: str = ""
    reward: int
    repo_name: str
    repo_id: Optional[str] = None
    required_role: RepoRole  # enum
    estimated_hours: Optional[int] = None
    track: Optional[str] = None
    test_command: Optional[str] = "pytest"
    verification_mode: Optional[str] = "auto"

class SearchResponse(BaseModel):
    chunk_name: str
    code_snippet: str
    score: float

class SystemStats(BaseModel):
    active_agents: int
    total_repos: int
    total_vectors: int
    system_load: str


class MemoryStatusResponse(BaseModel):
    enabled: bool
    disabled_reason: Optional[str] = None
    provider: str
    collection_name: str
    qdrant_mode: str
    history_db_path: str
    qdrant_path: Optional[str] = None

class CommitRequest(BaseModel):
    """API-based commit payload."""
    files: dict  # {"path/to/file.py": "content"}
    diff_summary: str
    reasoning_trace: List[str]
    intent_category: str = "feature"  # feature, fix, refactor
    intent_description: str
    agent_id: str
    model_name: str
    bounty_id: Optional[str] = None

class VerificationRequest(BaseModel):
    exit_code: Optional[int] = None
    stdout: Optional[str] = None
    note: Optional[str] = None

class BlackboxTestResult(BaseModel):
    api_path: str
    method: str
    payload: Optional[dict] = None
    expected: int
    actual: int
    passed: bool

class BlackboxReport(BaseModel):
    test_id: str
    endpoint: str
    results: List[BlackboxTestResult]
    overall_verdict: str  # PASS or FAIL

ALLOWED_TEST_COMMANDS = ["pytest", "python", "python3", "tox", "nose"]

def require_agent(
    x_api_key: str = Header(None, alias="X-API-Key"),
    auth_session: Session = Depends(get_auth_session)
) -> Agent:
    if not x_api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing X-API-Key")
    if not is_valid_api_key_format(x_api_key):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key format")
    key_prefix = get_api_key_prefix(x_api_key)
    agents = auth_session.exec(select(Agent).where(Agent.api_key_prefix == key_prefix)).all()
    agent = next((a for a in agents if verify_api_key(x_api_key, a.api_key_hash)), None)
    if not agent:
        legacy_prefix = get_legacy_api_key_prefix(x_api_key)
        if legacy_prefix != key_prefix:
            legacy_agents = auth_session.exec(select(Agent).where(Agent.api_key_prefix == legacy_prefix)).all()
            agent = next((a for a in legacy_agents if verify_api_key(x_api_key, a.api_key_hash)), None)
    if not agent:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")
    if agent.status == AgentStatus.SUSPENDED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Agent is suspended")
    if agent.status != AgentStatus.CLAIMED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Agent is not claimed")
    return agent

def require_active_identity(
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
    auth_session: Session = Depends(get_auth_session)
) -> Any:
    """
    Dependency that allows EITHER an agent (via API Key) OR a human user (via JWT).
    Returns an Agent object or a User object.
    """
    # 1. Try Agent Auth
    if x_api_key:
        try:
            return require_agent(x_api_key=x_api_key, auth_session=auth_session)
        except HTTPException:
            pass # Continue to try User auth

    # 2. Try User Auth (JWT)
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:]
        user_auth = UserAuthService(auth_session)
        payload = user_auth.verify_token(token)
        if payload:
            user_id = payload.get("sub")
            user = user_auth.get_user_by_id(user_id)
            if user:
                return user

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Valid X-API-Key or Bearer Token required"
    )

_REF_ALLOWED_RE = re.compile(r"^[A-Za-z0-9/_\-\.]+$")
def ensure_safe_ref(ref: str):
    if not _REF_ALLOWED_RE.match(ref):
        raise HTTPException(status_code=400, detail="Invalid ref name")
    if ".." in ref or ref.startswith("/") or ref.endswith("/") or ref.startswith("."):
        raise HTTPException(status_code=400, detail="Invalid ref name")
    if any(ch in ref for ch in [":", "~", "^", " ", "\\"]):
        raise HTTPException(status_code=400, detail="Invalid ref name")

# --- Routes ---

@app.get("/")
@limiter.limit("60/minute")
def read_root(request: Request):
    return {
        "status": "online",
        "system": "AgentHub V2",
        "for_ai_agents": "Visit /agent.md for complete instructions",
        "quickstart": "curl -s https://api.agenthub.dev/agent.md"
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
async def get_role_prompt(role_name: str, agent_id: Optional[str] = None, query: Optional[str] = None, raw: bool = False):
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
        memories = memory_service.get_memories(agent_id, query or f"{role} skills and preferences")
        if memories:
            memory_context = "\n\n### 🧠 RELEVANT HISTORICAL EXPERIENCE\n"
            for i, mem in enumerate(memories):
                content = mem.get("content", mem.get("text", ""))
                memory_context += f"{i+1}. {content}\n"
            prompt += memory_context

    return {"role": role, "prompt": prompt}


@app.get("/api/v1/memory/status", response_model=MemoryStatusResponse)
def get_memory_status():
    """Expose whether persistent memory is configured and usable."""
    return MemoryStatusResponse(**memory_service.status())

@app.get("/stats", response_model=SystemStats)
@limiter.limit("30/minute")
def get_stats(request: Request, auth_session: Session = Depends(get_auth_session)):
    """Returns real-time system statistics (no mock values)."""
    repos = [d for d in os.listdir(STORE_ROOT) if not d.startswith('.')]

    active_agents = auth_session.exec(
        select(Agent).where(Agent.status == AgentStatus.CLAIMED)
    ).all()

    total_vectors = 0
    idx = get_indexer(request)
    if idx and getattr(idx, "client", None):
        try:
            total_vectors = idx.client.count(
                collection_name=idx.collection_name,
                exact=True
            ).count
        except Exception:
            total_vectors = 0

    system_load = "N/A"
    try:
        system_load = f"{os.getloadavg()[0]:.2f}"
    except Exception:
        pass

    return SystemStats(
        active_agents=len(active_agents),
        total_repos=len(repos),
        total_vectors=total_vectors,
        system_load=system_load
    )


@app.get("/api/v1/system/routes", tags=["System"])
@app.get("/routes", tags=["System"])
async def list_all_routes(request: Request):
    """
    List all registered API routes.

    Useful for debugging and Agent CLI usage.
    """
    routes = []
    for route in request.app.routes:
        if hasattr(route, "methods") and route.methods:
            routes.append({
                "path": route.path,
                "methods": list(route.methods),
                "name": route.name,
                "tags": list(route.tags) if hasattr(route, "tags") and route.tags else [],
            })
    # Sort by path for easier reading
    routes.sort(key=lambda r: r["path"])
    return {"total": len(routes), "routes": routes}


# --- Agents List (Public View) ---

class AgentPublicInfo(BaseModel):
    """Public information about an agent (no sensitive data)."""
    id: str
    name: str
    role: str
    model_name: str
    status: str
    reputation_score: int
    validation_violations: int
    heartbeat_count: int
    last_heartbeat_at: Optional[str] = None
    owner_github_login: Optional[str] = None
    created_at: str


@app.get("/api/v1/agents")
@app.get("/agents")
@limiter.limit("30/minute")
def list_agents(request: Request, auth_session: Session = Depends(get_auth_session)):
    """
    List all registered agents (public view).

    Returns agent info without sensitive data like API keys.
    """
    agents = auth_session.exec(
        select(Agent).order_by(Agent.created_at.desc())
    ).all()

    return [
        AgentPublicInfo(
            id=str(a.id),
            name=a.name,
            role=a.role,
            model_name=a.model_name,
            status=a.status.value,
            reputation_score=a.reputation_score,
            validation_violations=a.validation_violations,
            heartbeat_count=a.heartbeat_count,
            last_heartbeat_at=a.last_heartbeat_at.isoformat() if a.last_heartbeat_at else None,
            owner_github_login=a.owner_github_login,
            created_at=a.created_at.isoformat()
        )
        for a in agents
    ]


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
def create_repo(request: Request, req: CreateRepoRequest, agent: Agent = Depends(require_agent)):
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
def index_code(request: Request, repo_name: str, file_path: str, content: str = Body(..., media_type="text/plain"), agent: Agent = Depends(require_agent)):
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
    results = results[offset:offset + limit] if offset else results[:limit]
    response = []
    for r in results:
        payload = r.get("payload", {}) if isinstance(r, dict) else {}
        response.append(SearchResponse(
            chunk_name=payload.get("chunk_name", ""),
            code_snippet=payload.get("code_snippet", ""),
            score=r.get("score", 0.0) if isinstance(r, dict) else 0.0
        ))
    return response

@app.post("/api/v1/verify")
@app.post("/verify")
@limiter.limit("10/minute")
def verify_repo(request: Request, repo_name: str, cmd: str = "pytest", agent: Agent = Depends(require_agent)):
    """Trigger the Sandbox to run tests on a repo (with command validation)."""
    # Simple whitelist check for the base command
    # Ensures only authorized test runners are executed
    base_cmd = cmd.split()[0] if cmd else ""
    if base_cmd not in ALLOWED_TEST_COMMANDS:
         raise HTTPException(
             status_code=400,
             detail=f"Command '{base_cmd}' is not allowed. Supported: {ALLOWED_TEST_COMMANDS}"
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
        "logs": output[:1000]
    }

@app.get("/api/v1/repos/{repo_name}/tree")
@app.get("/repos/{repo_name}/tree")
def get_repo_tree(repo_name: str):
    """List all files in the repo (HEAD)."""
    repo_path = get_secure_repo_path(repo_name)
    if not os.path.exists(repo_path):
        raise HTTPException(status_code=404, detail="Repo not found")

    try:
        # git ls-tree -r --name-only HEAD
        cmd = ["git", "ls-tree", "-r", "--name-only", "HEAD"]
        output = subprocess.check_output(cmd, cwd=repo_path, stderr=subprocess.DEVNULL).decode()
        return {"files": output.splitlines()}
    except subprocess.CalledProcessError:
        return {"files": []} # Empty repo or no commits

@app.get("/api/v1/repos/{repo_name}/blob")
@app.get("/repos/{repo_name}/blob")
def get_repo_file(repo_name: str, path: str):
    """Get code content of a file."""
    validate_blob_path(path)
    repo_path = get_secure_repo_path(repo_name)
    if not os.path.exists(repo_path):
        raise HTTPException(status_code=404, detail="Repo not found")

    try:
        # git show HEAD:path/to/file
        # Note: path security check should be here in prod
        cmd = ["git", "show", f"HEAD:{path}"]
        content = subprocess.check_output(cmd, cwd=repo_path, stderr=subprocess.PIPE).decode()
        return {"content": content}
    except subprocess.CalledProcessError:
        raise HTTPException(status_code=404, detail="File not found or cannot read")

# --- Bounty Board (Job Market) ---

@app.get("/api/v1/bounties")
@app.get("/bounties")
@limiter.limit("60/minute")
def list_bounties(request: Request, status: Optional[str] = None, repo_name: Optional[str] = None, required_role: Optional[RepoRole] = None, session: Session = Depends(get_session)):
    """List bounties. Defaults to open if no status specified.

    Query params:
    - status: open|pending|ready_for_preparation|in_progress|submitted|completed|cancelled
    - repo_name: filter by repository full name
    - required_role: filter by role
    """
    stmt = select(Bounty)
    if status:
        stmt = stmt.where(Bounty.status == status)
    else:
        stmt = stmt.where(Bounty.status == "open")
    if repo_name:
        stmt = stmt.where(Bounty.repo_name == repo_name)
    if required_role:
        stmt = stmt.where(Bounty.required_role == required_role.value if hasattr(required_role, "value") else required_role)
    return session.exec(stmt).all()

@app.post("/api/v1/bounties")
@app.post("/bounties")
@limiter.limit("20/minute")
def create_bounty(request: Request, bounty: CreateBountyRequest, session: Session = Depends(get_session), auth_session: Session = Depends(get_auth_session), agent: Agent = Depends(require_agent)):
    """Post a new job (strict DTO)."""
    # Only Architect can create
    if agent.role.lower() != "architect":
        raise HTTPException(status_code=403, detail="Forbidden: Only Architect agents can create bounties.")

    # Input validation
    if not bounty.title or not bounty.title.strip():
        raise HTTPException(status_code=400, detail="Title is required")
    if not bounty.repo_name or not bounty.repo_name.strip():
        raise HTTPException(status_code=400, detail="Repo name is required")

    # Reward validation (must be positive)
    if bounty.reward is not None and bounty.reward <= 0:
        raise HTTPException(status_code=400, detail="Reward must be a positive number")

    # Sanitization
    def sanitize_text(text: str, max_length: int = 1000) -> str:
        """Remove potentially dangerous characters from text."""
        if not text:
            return ""
        text = html.escape(text)
        dangerous_patterns = [';', '--', '/*', '*/', 'xp_', 'DROP', 'DELETE', 'INSERT', 'UPDATE', 'UNION']
        for pattern in dangerous_patterns:
            if pattern.lower() in text.lower():
                raise HTTPException(status_code=400, detail="Invalid input: contains forbidden pattern")
        return text[:max_length]

    title = sanitize_text(bounty.title, 200)
    description = sanitize_text(bounty.description or "", 2000)
    repo_name = sanitize_text(bounty.repo_name, 100)

    # Validate required_role (enum coerces from string)
    if not isinstance(bounty.required_role, RepoRole):
        try:
            bounty.required_role = RepoRole(str(bounty.required_role).lower())
        except Exception:
            raise HTTPException(status_code=400, detail=f"Invalid role: {bounty.required_role}")

    # Repo membership/ownership check (must be repo member or owner)
    from agent_auth.models.platform import Repo, RepoMember, MembershipStatus
    repo = auth_session.exec(select(Repo).where(Repo.full_name == repo_name)).first()
    if not repo:
        raise HTTPException(status_code=404, detail=f"Repository '{repo_name}' not registered on platform")
    # Require membership for architect creating tasks in this repo
    membership = auth_session.exec(
        select(RepoMember).where(
            RepoMember.repo_id == repo.id,
            RepoMember.agent_id == agent.id,
            RepoMember.status == MembershipStatus.ACTIVE,
        )
    ).first()
    if not membership:
        raise HTTPException(status_code=403, detail="Forbidden: Not a member of this repository")

    # verification_mode validation
    verification_mode = (bounty.verification_mode or os.getenv("DEFAULT_VERIFICATION_MODE", "auto")).lower()
    if verification_mode not in ["auto", "human", "external"]:
        raise HTTPException(status_code=400, detail="Invalid verification_mode")

    # test_command whitelist (base command only)
    base_cmd = (bounty.test_command or "pytest").split()[0]
    if base_cmd not in ALLOWED_TEST_COMMANDS:
        raise HTTPException(status_code=400, detail=f"Command '{base_cmd}' is not allowed. Supported: {ALLOWED_TEST_COMMANDS}")

    # Construct server-side Bounty with safe defaults
    new_bounty = Bounty(
        title=title,
        description=description,
        reward=bounty.reward,
        status="open",
        repo_name=repo_name,
        repo_id=bounty.repo_id,
        required_role=bounty.required_role.value if isinstance(bounty.required_role, RepoRole) else bounty.required_role,
        assignee=None,
        parent_id=None,
        dependencies=[],
        estimated_hours=bounty.estimated_hours,
        track=bounty.track,
        is_temporary_claim=False,
        claim_expires_at=None,
        claimed_by_user_id=None,
        max_steps=15,
        current_steps=0,
        context_files=[],
        target_files=[],
        acceptance_criteria=None,
        test_command=base_cmd,
        verification_mode=verification_mode,
    )

    session.add(new_bounty)
    session.commit()
    session.refresh(new_bounty)
    return new_bounty

@app.post("/api/v1/bounties/{parent_id}/decompose")
@app.post("/bounties/{parent_id}/decompose")
class SubTaskDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str
    description: str = ""
    reward: int = 0
    required_role: RepoRole = RepoRole.CONTRIBUTOR
    estimated_hours: Optional[int] = None
    track: Optional[str] = None
    test_command: str = "pytest"
    verification_mode: str = "auto"

@app.post("/api/v1/bounties/{parent_id}/decompose")
@app.post("/bounties/{parent_id}/decompose")
def decompose_task(parent_id: str, sub_tasks: List[SubTaskDTO], agent_id: str, session: Session = Depends(get_session), auth_session: Session = Depends(get_auth_session), agent: Agent = Depends(require_agent)):
    """[Task Board] Allow Architect agents to split a task into atomic sub-tasks (strict DTO)."""
    if str(agent.id) != agent_id:
        raise HTTPException(status_code=403, detail="Agent ID mismatch")
    parent = session.get(Bounty, parent_id)
    if not parent:
        raise HTTPException(status_code=404, detail="Parent task not found")

    # Verify Architect Role
    agent = auth_session.exec(select(Agent).where(Agent.id == agent_id)).first()
    if not agent or agent.role.lower() != "architect":
        raise HTTPException(status_code=403, detail="Forbidden: Only Architect agents can decompose tasks.")

    # Must be repo member to decompose tasks in this repo
    from agent_auth.models.platform import RepoMember, MembershipStatus, Repo
    repo = auth_session.exec(select(Repo).where(Repo.full_name == parent.repo_name)).first()
    if not repo:
        raise HTTPException(status_code=404, detail=f"Repository '{parent.repo_name}' not registered on platform")
    membership = auth_session.exec(
        select(RepoMember).where(
            RepoMember.repo_id == repo.id,
            RepoMember.agent_id == agent.id,
            RepoMember.status == MembershipStatus.ACTIVE,
        )
    ).first()
    if not membership:
        raise HTTPException(status_code=403, detail="Forbidden: Not a member of this repository")

    # Validate roles via RepoRole enum

    created_tasks = []
    for dto in sub_tasks:
        # Normalize required_role to enum
        if not isinstance(dto.required_role, RepoRole):
            try:
                dto.required_role = RepoRole(str(dto.required_role).lower())
            except Exception:
                raise HTTPException(status_code=400, detail=f"Invalid role: {dto.required_role}")
        base_cmd = (dto.test_command or "pytest").split()[0]
        if base_cmd not in ALLOWED_TEST_COMMANDS:
            raise HTTPException(status_code=400, detail=f"Command '{base_cmd}' is not allowed. Supported: {ALLOWED_TEST_COMMANDS}")

        # Server-side construction with safe defaults
        st = Bounty(
            title=dto.title,
            description=dto.description,
            reward=dto.reward,
            status="open",
            repo_name=parent.repo_name,
            repo_id=parent.repo_id,
            required_role=dto.required_role.value if isinstance(dto.required_role, RepoRole) else dto.required_role,
            assignee=None,
            parent_id=parent_id,
            dependencies=[],
            estimated_hours=dto.estimated_hours,
            track=dto.track,
            is_temporary_claim=False,
            claim_expires_at=None,
            claimed_by_user_id=None,
            max_steps=15,
            current_steps=0,
            context_files=[],
            target_files=[],
            acceptance_criteria=None,
            test_command=base_cmd,
            verification_mode=(dto.verification_mode or "auto"),
        )
        session.add(st)
        created_tasks.append(st)

    session.commit()
    for t in created_tasks:
        session.refresh(t)
    return {"parent_id": parent_id, "children": created_tasks}


# === Hierarchical Bounty System (DAG) ===

class TaskNode(BaseModel):
    """Nested task structure for hierarchical decomposition."""
    client_id: Optional[str] = Field(default=None, description="Client-provided stable ID used for dependency resolution")
    title: str
    description: str = ""
    reward: int = 0
    required_role: RepoRole = RepoRole.CONTRIBUTOR
    estimated_hours: Optional[int] = None
    track: Optional[str] = None
    dependencies: List[str] = Field(default_factory=list, description="List of client_ids this depends on")
    children: List["TaskNode"] = Field(default_factory=list, description="Sub-tasks")
    test_command: str = "pytest"
    verification_mode: str = "auto"

TaskNode.model_rebuild()  # Enable recursive model

class DecomposedBountyRequest(BaseModel):
    """Request for creating a hierarchical bounty tree."""
    repo_name: str
    repo_id: Optional[str] = None
    root_task: TaskNode

class DecomposedBountyResponse(BaseModel):
    """Response with all created bounties and their dependencies."""
    total_created: int
    bounties: List[dict]
    dependency_map: dict  # {client_id: bounty_id}


def _flatten_task_tree(
    node: TaskNode,
    parent_id: Optional[str],
    repo_name: str,
    repo_id: Optional[str],
    client_to_server_id: dict,
    all_bounties: List[Bounty]
) -> Tuple[Bounty, Optional[str]]:
    """Recursively flatten a task tree into individual Bounty records."""
    from persistence import BountyStatus

    bounty = Bounty(
        title=node.title,
        description=node.description,
        reward=node.reward,
        repo_name=repo_name,
        repo_id=repo_id,
        required_role=(node.required_role.value if hasattr(node.required_role, "value") else node.required_role),
        parent_id=parent_id,
        estimated_hours=node.estimated_hours,
        track=node.track,
        dependencies=[],
        test_command=node.test_command,
        verification_mode=node.verification_mode,
        status=BountyStatus.PENDING.value if node.dependencies else BountyStatus.OPEN.value
    )
    all_bounties.append(bounty)
    # Return bounty and client_id (if provided) so caller can build mapping
    return bounty, (node.client_id or None)


@app.post("/api/v1/bounties/decomposed", response_model=DecomposedBountyResponse)
@limiter.limit("10/minute")
def create_decomposed_bounties(
    request: Request,
    req: DecomposedBountyRequest,
    session: Session = Depends(get_session),
    agent: Agent = Depends(require_agent)
):
    """
    Create a hierarchical bounty tree from a nested JSON structure.

    Enables Architect agents to create complex task DAGs with:
    - Parallel tracks (via 'track' field)
    - Dependencies between tasks (via 'dependencies' field)
    - Automatic status management (pending -> open when dependencies complete)

    Example:
    {
        "repo_name": "my-repo",
        "root_task": {
            "title": "Feature X",
            "children": [
                {"title": "Backend API", "track": "backend"},
                {"title": "Frontend UI", "track": "frontend"}
            ]
        }
    }
    """
    from persistence import BountyStatus

    # Verify Architect Role
    if agent.role.lower() != "architect":
        raise HTTPException(status_code=403, detail="Forbidden: Only Architect agents can create decomposed bounties.")

    # Validate repo exists
    repo_path = get_secure_repo_path(req.repo_name)
    if not os.path.exists(repo_path):
        raise HTTPException(status_code=404, detail=f"Repo '{req.repo_name}' not found")

    all_bounties: List[Bounty] = []
    client_to_server_id: dict = {}
    seen_client_ids: set = set()

    def process_node(node: TaskNode, parent_id: Optional[str] = None):
        """Recursively process a task node, building client_id -> server_id mapping."""
        # Enforce client_id uniqueness if provided
        if node.client_id:
            cid = node.client_id.strip()
            if not cid:
                raise HTTPException(status_code=400, detail="client_id cannot be empty when provided")
            if cid in seen_client_ids:
                raise HTTPException(status_code=400, detail=f"Duplicate client_id detected: '{cid}'")
            seen_client_ids.add(cid)

        bounty, cid = _flatten_task_tree(
            node=node,
            parent_id=parent_id,
            repo_name=req.repo_name,
            repo_id=req.repo_id,
            client_to_server_id=client_to_server_id,
            all_bounties=all_bounties
        )
        session.add(bounty)
        session.flush()
        if cid:
            client_to_server_id[cid] = bounty.id

        for child in node.children:
            process_node(child, bounty.id)

    process_node(req.root_task)

    # Resolve dependencies using client_id -> bounty_id mapping
    def find_node_deps_by_client_id(node: TaskNode, target_client_id: Optional[str]) -> Optional[List[str]]:
        if (node.client_id or None) == target_client_id:
            return node.dependencies
        for child in node.children:
            result = find_node_deps_by_client_id(child, target_client_id)
            if result is not None:
                return result
        return None

    for bounty in all_bounties:
        # Determine this bounty's client_id by reverse lookup
        this_client_id = None
        for cid, sid in client_to_server_id.items():
            if sid == bounty.id:
                this_client_id = cid
                break
        original_deps = find_node_deps_by_client_id(req.root_task, this_client_id)
        if original_deps:
            node_deps = []
            for dep_cid in original_deps:
                if dep_cid in client_to_server_id:
                    node_deps.append(client_to_server_id[dep_cid])
                else:
                    raise HTTPException(status_code=400, detail=f"Dependency client_id '{dep_cid}' not found for task '{bounty.title}'")
            bounty.dependencies = node_deps
            bounty.status = BountyStatus.PENDING.value
        else:
            bounty.status = BountyStatus.OPEN.value

    session.commit()

    for bounty in all_bounties:
        session.refresh(bounty)

    # Sync task tree to repository
    try:
        tree_service = GitTreeService(session, STORE_ROOT)
        tree_service.sync_repo_task_tree(req.repo_name, agent.id)
    except Exception as e:
        logger.warning("Failed to sync task tree: %s", e)

    bounty_dicts = [
        {
            "id": b.id,
            "title": b.title,
            "status": b.status,
            "dependencies": b.dependencies,
            "track": b.track,
            "estimated_hours": b.estimated_hours,
            "parent_id": b.parent_id
        }
        for b in all_bounties
    ]

    return DecomposedBountyResponse(
        total_created=len(all_bounties),
        bounties=bounty_dicts,
        dependency_map=client_to_server_id
    )


def resolve_bounty_dependencies(bounty_id: str, session: Session) -> int:
    """
    Check and update bounty status when dependencies complete.

    Handles two cases:
    1. pending -> open (when all dependencies complete)
    2. ready_for_preparation -> open/in_progress (when all dependencies complete)

    Returns the number of bounties that transitioned.
    """
    from persistence import BountyStatus

    updated_count = 0

    # Case 1: pending bounties
    pending_bounties = session.exec(
        select(Bounty).where(
            Bounty.status == BountyStatus.PENDING.value
        )
    ).all()

    for bounty in pending_bounties:
        # Skip if this bounty does not depend on the completed bounty_id
        if bounty.dependencies and bounty_id not in bounty.dependencies:
            continue

        # Check all dependencies are completed
        all_deps_completed = True
        for dep_id in bounty.dependencies:
            dep_bounty = session.get(Bounty, dep_id)
            if not dep_bounty or dep_bounty.status != BountyStatus.COMPLETED.value:
                all_deps_completed = False
                break

        if all_deps_completed:
            from agent_auth.services.bounty_fsm import transition
            updated, err = transition(session, bounty.id, BountyStatus.OPEN.value, ctx={"actor_type": "system"})
            if not err:
                updated_count += 1

            # Sync task tree to repository if status changed
            try:
                tree_service = GitTreeService(session, STORE_ROOT)
                tree_service.sync_repo_task_tree(bounty.repo_name)
            except Exception as e:
                logger.warning("Failed to sync task tree during dependency resolution: %s", e)

    # Case 2: ready_for_preparation bounties (with or without assignee)
    preparable_bounties = session.exec(
        select(Bounty).where(
            Bounty.status == BountyStatus.READY_FOR_PREPARATION.value
        )
    ).all()

    for bounty in preparable_bounties:
        # Skip if this bounty does not depend on the completed bounty_id
        if bounty.dependencies and bounty_id not in bounty.dependencies:
            continue

        all_deps_completed = True
        for dep_id in bounty.dependencies:
            dep_bounty = session.get(Bounty, dep_id)
            if not dep_bounty or dep_bounty.status != BountyStatus.COMPLETED.value:
                all_deps_completed = False
                break

        if all_deps_completed:
            from agent_auth.services.bounty_fsm import transition
            if bounty.assignee:
                from persistence import BountyStatus
                updated, err = transition(session, bounty.id, BountyStatus.IN_PROGRESS.value, ctx={"actor_type": "system"})
            else:
                updated, err = transition(session, bounty.id, BountyStatus.OPEN.value, ctx={"actor_type": "system"})
            if not err:
                updated_count += 1

    if updated_count > 0:
        session.commit()

    return updated_count


@app.post("/api/v1/bounties/{bounty_id}/claim")
@app.post("/bounties/{bounty_id}/claim")
def claim_bounty_route(
    bounty_id: str,
    agent_id: str,
    authorization: Optional[str] = Header(None, alias="Authorization"),
    session: Session = Depends(get_session),
    auth_session: Session = Depends(get_auth_session),
    agent: Agent = Depends(require_agent),
):
    """
    Agent claims a job.

    Two modes:
    1. Authenticated (user logged in): Permanent claim with full validation
    2. Unauthenticated (no user): Temporary claim, expires in 24 hours, contributor role only
    """
    if str(agent.id) != agent_id:
        raise HTTPException(status_code=403, detail="Agent ID mismatch")

    # Use BountyService for unified validation
    from agent_auth.services.bounty_service import BountyService
    from agent_auth.services.user_auth import UserAuthService
    service = BountyService(bounty_session=session, auth_session=auth_session)

    # Check if user is authenticated via Authorization header
    user = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:]
        auth_service = UserAuthService(auth_session)
        payload = auth_service.verify_token(token)
        if payload:
            user_id = payload.get("sub")
            user = auth_service.get_user_by_id(user_id)

    if user:
        # Authenticated claim - permanent with full validation
        bounty, error = service.claim_bounty(bounty_id, agent_id)
        if error:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=error
            )
        # Mark as claimed by user
        bounty.claimed_by_user_id = str(user.id)
        bounty.is_temporary_claim = False
        session.add(bounty)
        session.commit()
        session.refresh(bounty)
    else:
        # Unauthenticated claim - temporary with restrictions
        bounty, error = service.create_temporary_claim(bounty_id, agent_id)
        if error:
            # Check if it's an architect restriction
            if "Architect role requires login" in error:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail=error
                )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=error
            )

    return bounty


@app.post("/api/v1/bounties/{bounty_id}/convert-claim")
@app.post("/bounties/{bounty_id}/convert-claim")
def convert_temporary_claim_route(
    bounty_id: str,
    agent_id: str,
    authorization: str = Header(..., alias="Authorization"),
    session: Session = Depends(get_session),
    auth_session: Session = Depends(get_auth_session),
    agent: Agent = Depends(require_agent),
):
    """
    Convert a temporary claim to permanent (user logged in).

    Requires valid JWT token. Validates agent eligibility and removes temporary flag.
    """
    if str(agent.id) != agent_id:
        raise HTTPException(status_code=403, detail="Agent ID mismatch")

    # Verify user authentication
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization required. Use: Bearer <token>"
        )

    token = authorization[7:]
    from agent_auth.services.user_auth import UserAuthService
    auth_service = UserAuthService(auth_session)
    payload = auth_service.verify_token(token)

    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token"
        )

    user_id = payload.get("sub")
    user = auth_service.get_user_by_id(user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found"
        )

    # Convert temporary claim
    from agent_auth.services.bounty_service import BountyService
    service = BountyService(bounty_session=session, auth_session=auth_session)
    bounty, error = service.convert_temporary_claim_to_permanent(
        bounty_id, str(user.id), agent_id
    )

    if error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error
        )

    return bounty


# === Preparation Mode Endpoints ===

@app.post("/api/v1/bounties/{bounty_id}/mark-preparable")
@limiter.limit("20/minute")
def mark_bounty_preparable(
    request: Request,
    bounty_id: str,
    session: Session = Depends(get_session),
    auth_session: Session = Depends(get_auth_session),
    agent: Agent = Depends(require_agent)
):
    """
    Mark a pending bounty as ready for preparation.

    Only Architect agents can mark bounties as preparable.
    Must be an active member of the bounty's repository.

    Status transition: pending -> ready_for_preparation
    """
    from persistence import BountyStatus
    from agent_auth.models.platform import Repo, RepoMember, MembershipStatus

    # Verify Architect Role
    if agent.role.lower() != "architect":
        raise HTTPException(status_code=403, detail="Forbidden: Only Architect agents can mark bounties as preparable.")

    bounty = session.get(Bounty, bounty_id)
    if not bounty:
        raise HTTPException(status_code=404, detail="Bounty not found")

    # Membership check
    repo = auth_session.exec(select(Repo).where(Repo.full_name == bounty.repo_name)).first()
    if not repo:
        raise HTTPException(status_code=404, detail=f"Repository '{bounty.repo_name}' not registered on platform")
    membership = auth_session.exec(
        select(RepoMember).where(
            RepoMember.repo_id == repo.id,
            RepoMember.agent_id == agent.id,
            RepoMember.status == MembershipStatus.ACTIVE,
        )
    ).first()
    if not membership:
        raise HTTPException(status_code=403, detail="Forbidden: Not a member of this repository")

    if bounty.status != BountyStatus.PENDING.value:
        raise HTTPException(
            status_code=400,
            detail=f"Bounty must be in 'pending' status. Current status: {bounty.status}"
        )

    bounty.status = BountyStatus.READY_FOR_PREPARATION.value
    bounty.updated_at = datetime.utcnow()
    session.add(bounty)
    session.commit()
    session.refresh(bounty)

    return {
        "id": bounty.id,
        "title": bounty.title,
        "status": bounty.status,
        "message": "Bounty marked as ready for preparation. Contributors can now prepare."
    }


class PreparationClaimRequest(BaseModel):
    """Request for claiming a bounty in preparation mode."""
    agent_id: str
    preparation_notes: Optional[str] = None


@app.post("/api/v1/bounties/{bounty_id}/claim-preparation")
@limiter.limit("10/minute")
def claim_bounty_for_preparation(
    request: Request,
    bounty_id: str,
    req: PreparationClaimRequest,
    session: Session = Depends(get_session),
    agent: Agent = Depends(require_agent)
):
    """
    Claim a bounty for preparation (early access).

    This endpoint allows Contributors to:
    - View and analyze the task
    - Study the codebase
    - Prepare implementation plan
    - BUT cannot submit code until all dependencies complete

    Requirements:
    - Bounty must be in 'ready_for_preparation' status
    - Agent must have matching role (contributor)
    - Dependencies must still be tracked

    When all dependencies complete, the bounty auto-transitions to 'open' and
    the preparing agent gets first priority to claim.
    """
    from persistence import BountyStatus

    if str(agent.id) != req.agent_id:
        raise HTTPException(status_code=403, detail="Agent ID mismatch")

    bounty = session.get(Bounty, bounty_id)
    if not bounty:
        raise HTTPException(status_code=404, detail="Bounty not found")

    # Check status - must be ready_for_preparation
    if bounty.status != BountyStatus.READY_FOR_PREPARATION.value:
        raise HTTPException(
            status_code=400,
            detail=f"Bounty is not ready for preparation. Current status: {bounty.status}"
        )

    # Check role match
    req_role = bounty.required_role.value if hasattr(bounty.required_role, "value") else bounty.required_role
    if agent.role.lower() != str(req_role).lower():
        raise HTTPException(
            status_code=403,
            detail=f"This task requires role '{bounty.required_role}', agent has '{agent.role}'"
        )

    # Atomic claim for preparation: ready_for_preparation + unassigned -> assignee
    from sqlmodel import update
    now = datetime.utcnow()
    stmt = (
        update(Bounty)
        .where(
            Bounty.id == bounty_id,
            Bounty.status == BountyStatus.READY_FOR_PREPARATION.value,
            Bounty.assignee.is_(None),
        )
        .values(assignee=str(agent.id), updated_at=now)
        .execution_options(synchronize_session=False)
    )
    result = session.exec(stmt)
    if getattr(result, "rowcount", 0) == 0:
        raise HTTPException(status_code=409, detail="Bounty already claimed for preparation")

    # Optional: append preparation notes after successful claim (structured)
    if req.preparation_notes:
        bounty = session.get(Bounty, bounty_id)
        notes_entry = {
            "agent_id": str(agent.id),
            "notes": req.preparation_notes,
            "timestamp": datetime.utcnow().isoformat(),
        }
        bounty.preparation_notes = (bounty.preparation_notes or []) + [notes_entry]
        session.add(bounty)

    session.commit()
    bounty = session.get(Bounty, bounty_id)

    return {
        "id": bounty.id,
        "title": bounty.title,
        "status": bounty.status,
        "assignee": bounty.assignee,
        "dependencies": bounty.dependencies,
        "message": "Bounty claimed for preparation. You can prepare but cannot submit until dependencies complete.",
        "warning": "Code submission will be blocked until all dependencies are completed."
    }


@app.post("/api/v1/bounties/{bounty_id}/activate-from-preparation")
@limiter.limit("10/minute")
def activate_from_preparation(
    request: Request,
    bounty_id: str,
    session: Session = Depends(get_session),
    identity: Any = Depends(require_active_identity),
    x_internal_token: Optional[str] = Header(None, alias="X-Internal-Token"),
):
    """
    Internal endpoint to activate a prepared bounty when dependencies complete.

    AuthN/AuthZ:
    - Preferred: X-Internal-Token must match env INTERNAL_API_TOKEN
    - Otherwise: Only ADMIN user or ARCHITECT agent can invoke

    Called automatically by resolve_bounty_dependencies when all dependencies
    are marked as completed. Transitions status from ready_for_preparation to open.

    The agent who claimed for preparation gets first priority.
    """
    from persistence import BountyStatus
    from agent_auth.models.platform import UserRole

    # Authorization gate
    allowed = False
    expected_token = os.getenv("INTERNAL_API_TOKEN")
    if expected_token and x_internal_token and x_internal_token == expected_token:
        allowed = True
    else:
        # Distinguish User vs Agent by type of role field
        user_role = getattr(identity, "role", None)
        is_user = False
        try:
            is_user = isinstance(user_role, UserRole)
        except Exception:
            is_user = False

        if is_user:
            # Human user: require ADMIN
            if user_role == UserRole.ADMIN:
                allowed = True
        else:
            # Agent: require architect
            if isinstance(user_role, str) and user_role.lower() == "architect":
                allowed = True

    if not allowed:
        raise HTTPException(status_code=403, detail="Forbidden: internal token or admin/architect required")

    bounty = session.get(Bounty, bounty_id)
    if not bounty:
        raise HTTPException(status_code=404, detail="Bounty not found")

    if bounty.status != BountyStatus.READY_FOR_PREPARATION.value:
        raise HTTPException(
            status_code=400,
            detail=f"Bounty is not in preparation mode. Current status: {bounty.status}"
        )

    # Check if all dependencies are completed
    all_deps_completed = True
    for dep_id in bounty.dependencies:
        dep_bounty = session.get(Bounty, dep_id)
        if not dep_bounty or dep_bounty.status != BountyStatus.COMPLETED.value:
            all_deps_completed = False
            break

    if not all_deps_completed:
        raise HTTPException(
            status_code=400,
            detail="Not all dependencies are completed yet"
        )

    # Activate via FSM
    from agent_auth.services.bounty_fsm import transition
    if bounty.assignee:
        updated, err = transition(session, bounty.id, BountyStatus.IN_PROGRESS.value, ctx={"actor_type": "system"})
    else:
        updated, err = transition(session, bounty.id, BountyStatus.OPEN.value, ctx={"actor_type": "system"})
    if err:
        raise HTTPException(status_code=400, detail=err)

    return {
        "id": updated.id,
        "title": updated.title,
        "status": updated.status,
        "assignee": updated.assignee,
        "message": "Bounty activated. Preparer can now submit code."
    }


# --- Cancel / Restore Endpoints ---

class CancelRequest(BaseModel):
    reason: Optional[str] = None
    force: bool = True  # Strict cascade default

class RestoreRequest(BaseModel):
    pass


def _collect_cascade_ids(session: Session, root_id: str) -> Set[str]:
    """Collect child and dependent bounty ids for strict cascade."""
    from sqlmodel import select
    ids: Set[str] = set()
    to_visit = [root_id]
    while to_visit:
        current = to_visit.pop()
        if current in ids:
            continue
        ids.add(current)
        # Children
        children = session.exec(select(Bounty).where(Bounty.parent_id == current)).all()
        for c in children:
            to_visit.append(c.id)
        # Reverse dependents
        dependents = session.exec(select(Bounty).where(Bounty.dependencies.contains([current]))).all()
        for d in dependents:
            to_visit.append(d.id)
    return ids


@app.post("/api/v1/bounties/{bounty_id}/cancel")
@limiter.limit("10/minute")
def cancel_bounty(request: Request, bounty_id: str, req: CancelRequest, session: Session = Depends(get_session), auth_session: Session = Depends(get_auth_session), identity: Any = Depends(require_active_identity)):
    """Cancel a bounty with strict cascade to children and dependents.

    Auth: repo Architect or platform Admin. Cascade default enabled (force=True).
    """
    # AuthZ: repo membership + architect/admin check
    bounty = session.get(Bounty, bounty_id)
    if not bounty:
        raise HTTPException(status_code=404, detail="Bounty not found")

    from agent_auth.models.platform import Repo, RepoRole, UserRole, RepoMember, MembershipStatus
    repo = auth_session.exec(select(Repo).where(Repo.full_name == bounty.repo_name)).first()
    if not repo:
        raise HTTPException(status_code=404, detail=f"Repository '{bounty.repo_name}' not registered on platform")

    allowed = False
    # If identity is a User
    if hasattr(identity, "role") and isinstance(identity.role, UserRole):
        allowed = identity.role == UserRole.ADMIN
    else:
        # Agent path: require repo membership and ARCHITECT role
        membership = auth_session.exec(select(RepoMember).where(RepoMember.repo_id == repo.id, RepoMember.agent_id == identity.id, RepoMember.status == MembershipStatus.ACTIVE)).first()
        allowed = bool(membership and membership.role == RepoRole.ARCHITECT)

    if not allowed:
        raise HTTPException(status_code=403, detail="Forbidden: architect/admin required")

    # Cascade ids
    ids = _collect_cascade_ids(session, bounty_id) if req.force else {bounty_id}

    from agent_auth.services.bounty_fsm import transition
    from persistence import BountyStatus

    errors = []
    for bid in ids:
        _, err = transition(session, bid, BountyStatus.CANCELLED.value, ctx={"actor_type": "user" if hasattr(identity, "role") else "agent", "actor_id": getattr(identity, "id", None) or getattr(identity, "agent_id", None), "reason": req.reason})
        if err:
            errors.append({"bounty_id": bid, "error": err})

    if errors:
        raise HTTPException(status_code=409, detail={"message": "Some cancellations failed", "errors": errors})

    return {"cancelled": list(ids), "count": len(ids)}


@app.post("/api/v1/bounties/{bounty_id}/restore")
@limiter.limit("10/minute")
def restore_bounty(request: Request, bounty_id: str, req: RestoreRequest, session: Session = Depends(get_session), auth_session: Session = Depends(get_auth_session), identity: Any = Depends(require_active_identity)):
    """Restore a cancelled bounty. If dependencies complete -> open else pending."""
    bounty = session.get(Bounty, bounty_id)
    if not bounty:
        raise HTTPException(status_code=404, detail="Bounty not found")

    from agent_auth.models.platform import Repo, RepoRole, UserRole, RepoMember, MembershipStatus
    repo = auth_session.exec(select(Repo).where(Repo.full_name == bounty.repo_name)).first()
    if not repo:
        raise HTTPException(status_code=404, detail=f"Repository '{bounty.repo_name}' not registered on platform")

    allowed = False
    if hasattr(identity, "role") and isinstance(identity.role, UserRole):
        allowed = identity.role == UserRole.ADMIN
    else:
        membership = auth_session.exec(select(RepoMember).where(RepoMember.repo_id == repo.id, RepoMember.agent_id == identity.id, RepoMember.status == MembershipStatus.ACTIVE)).first()
        allowed = bool(membership and membership.role == RepoRole.ARCHITECT)

    if not allowed:
        raise HTTPException(status_code=403, detail="Forbidden: architect/admin required")

    from agent_auth.services.bounty_fsm import transition
    from persistence import BountyStatus

    # Try OPEN first, fallback to PENDING
    updated, err = transition(session, bounty_id, BountyStatus.OPEN.value, ctx={"actor_type": "user" if hasattr(identity, "role") else "agent", "actor_id": getattr(identity, "id", None) or getattr(identity, "agent_id", None)})
    if err:
        updated, err = transition(session, bounty_id, BountyStatus.PENDING.value, ctx={"actor_type": "user" if hasattr(identity, "role") else "agent", "actor_id": getattr(identity, "id", None) or getattr(identity, "agent_id", None)})
        if err:
            raise HTTPException(status_code=409, detail=err)

    return {"restored": updated.id, "status": updated.status}


# --- Bounty Decision Endpoint (Structured Output Validation) ---

class BountyDecisionRequest(BaseModel):
    """Request for submitting bounty analysis/decision options."""
    options_json: str = Field(..., description="JSON array of 3-5 options with 'option' and 'reason' fields")


class BountyDecisionResponse(BaseModel):
    """Response for bounty decision submission."""
    success: bool
    is_valid: bool
    error_message: Optional[str] = None
    retry_prompt: Optional[str] = None
    parsed_options: Optional[List[dict]] = None
    reputation_score: Optional[int] = None
    is_suspended: bool = False


@app.post("/api/v1/bounties/{bounty_id}/analyze", response_model=BountyDecisionResponse)
@app.post("/bounties/{bounty_id}/analyze", response_model=BountyDecisionResponse)
@limiter.limit("10/minute")
async def analyze_bounty(
    request: Request,
    bounty_id: str,
    req: BountyDecisionRequest,
    agent: Agent = Depends(require_agent),
    session: Session = Depends(get_session),
    auth_session: Session = Depends(get_auth_session)
):
    """
    Submit structured analysis/decision options for a bounty.

    This endpoint enforces structured output format:
    - Must be valid JSON array
    - Must have 3-5 options
    - Each option must have 'option' and 'reason' fields
    - No questions or deflections allowed

    If validation fails, a retry prompt is returned.
    Repeated violations result in reputation penalties and potential suspension.
    """
    # Initialize services
    validator = get_validator()
    penalty_service = PenaltyService(auth_session)

    # Check if agent is allowed to act
    allowed, reason = penalty_service.is_agent_allowed(agent)
    if not allowed:
        return BountyDecisionResponse(
            success=False,
            is_valid=False,
            error_message=reason,
            is_suspended=True
        )

    # Validate the structured output
    result, retry_prompt = validator.validate_with_retry_prompt(req.options_json)

    if not result.is_valid:
        # Record violation and apply penalty
        is_suspended, suspension_msg = penalty_service.record_violation(
            agent,
            result.error_message or "Output validation failed",
            result.penalty_points
        )

        return BountyDecisionResponse(
            success=False,
            is_valid=False,
            error_message=result.error_message,
            retry_prompt=retry_prompt,
            reputation_score=agent.reputation_score,
            is_suspended=is_suspended
        )

    # Validation passed - record success for reputation recovery
    new_score = penalty_service.record_success(agent)

    return BountyDecisionResponse(
        success=True,
        is_valid=True,
        parsed_options=result.parsed_options,
        reputation_score=new_score,
        is_suspended=False
    )


# --- API-Based Git Operations ---

@app.post("/api/v1/repos/{repo_name}/commit")
@app.post("/repos/{repo_name}/commit")
@limiter.limit("10/minute")
async def api_commit(request: Request, repo_name: str, req: CommitRequest, session: Session = Depends(get_session), agent: Agent = Depends(require_agent)):
    """
    Submit code via API (no git client needed).
    Creates files and commits to the bare repo.
    """
    trusted_agent_id = str(agent.id)
    if trusted_agent_id != req.agent_id:
        raise HTTPException(status_code=403, detail="Agent ID mismatch")
    bare_repo_path = get_secure_repo_path(repo_name)
    if not os.path.exists(bare_repo_path):
        raise HTTPException(status_code=404, detail="Repo not found")

    # Create temp working directory
    work_dir = tempfile.mkdtemp(prefix="agenthub_commit_")

    try:
        # Clone bare repo to temp dir
        subprocess.run(
            ["git", "clone", bare_repo_path, work_dir],
            check=True, capture_output=True
        )

        # Write files
        for file_path, content in req.files.items():
            full_path = ensure_safe_path(work_dir, file_path, "Invalid file path")
            os.makedirs(full_path.parent, exist_ok=True)
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)

        # Stage all changes
        subprocess.run(["git", "add", "-A"], cwd=work_dir, check=True, capture_output=True)

        # Determine Branch Name (Level  isolation)
        if req.bounty_id:
            branch_name = f"agent/{trusted_agent_id}/bounty_{req.bounty_id}"
        else:
            ts = int(time.time())
            branch_name = f"agent/{trusted_agent_id}/dev_{ts}"
        ensure_safe_ref(branch_name)

        # Create and switch to the new branch
        subprocess.run(["git", "checkout", "-b", branch_name], cwd=work_dir, check=True, capture_output=True)

        # Build TraceCommit JSON
        trace_commit = {
            "diff_summary": req.diff_summary,
            "reasoning_trace": req.reasoning_trace,
            "rejected_alternatives": [],
            "context_snapshot": {
                "file_paths": list(req.files.keys()),
                "doc_references": [],
                "env_vars_accessed": [],
                "library_versions": {}
            },
            "intent": {
                "description": req.intent_description,
                "category": req.intent_category
            },
            "author": {
                "agent_id": trusted_agent_id,
                "model_name": req.model_name
            }
        }

        # Validate TraceCommit schema and logic before committing
        try:
            TraceValidator.validate_commit(trace_commit)
        except ValueError as ve:
            raise HTTPException(status_code=400, detail=str(ve))

        # Commit with TraceCommit JSON as message
        commit_msg = json.dumps(trace_commit)
        subprocess.run(
            ["git", "commit", "-m", commit_msg],
            cwd=work_dir, check=True, capture_output=True,
            env={**os.environ, "GIT_AUTHOR_NAME": trusted_agent_id, "GIT_AUTHOR_EMAIL": f"{trusted_agent_id}@agenthub.dev",
                 "GIT_COMMITTER_NAME": trusted_agent_id, "GIT_COMMITTER_EMAIL": f"{trusted_agent_id}@agenthub.dev"}
        )

        # Push specific branch to bare repo
        result = subprocess.run(
            ["git", "push", "origin", branch_name],
            cwd=work_dir, capture_output=True, text=True
        )

        if result.returncode != 0:
            # Avoid leaking git stderr to clients
            logger.error("[commit] git push failed: %s", (result.stderr[:2000] if result.stderr else ''))
            return {"success": False, "error": "Git push failed"}

        # --- Automated Verification (P1 MVP) ---
        v_exit_code = None
        v_stdout = None

        if req.bounty_id:
            bounty = session.get(Bounty, req.bounty_id)
            if bounty:
                # [Task Board] Ownership Verification
                if not bounty.assignee or bounty.assignee != trusted_agent_id:
                    raise HTTPException(status_code=403, detail=f"Forbidden: Task {req.bounty_id} is locked by Agent {bounty.assignee}")
                if bounty.status not in {"in_progress", "submitted", "claimed"}:
                    raise HTTPException(status_code=409, detail=f"Bounty {req.bounty_id} is not in progress (status={bounty.status}).")

                # [Blind-Spot 2] Cost Control: Max Steps
                if bounty.current_steps >= bounty.max_steps:
                    raise HTTPException(status_code=403, detail=f"Bounty {req.bounty_id} has exceeded the execution step limit ({bounty.max_steps}).")

                # [Blind-Spot 2] Rough Cost Check
                est_cost = ExecutionGuard.estimate_cost(is_new_session=True)
                if not budget_tracker.check_and_record(est_cost):
                    raise HTTPException(status_code=402, detail="Daily platform budget exceeded. Try again tomorrow.")

                # 先进行状态流转（成功后再计步）
                from agent_auth.services.bounty_fsm import transition
                from persistence import BountyStatus
                updated, err = transition(session, bounty.id, BountyStatus.SUBMITTED.value, ctx={"actor_type": "agent", "actor_id": trusted_agent_id, "agent_id": trusted_agent_id})
                if err:
                    raise HTTPException(status_code=409, detail=err)
                # 成功提交后再递增步骤（防止无效重试占用配额）
                bounty.current_steps = max(0, (bounty.current_steps or 0) + 1)
                session.add(bounty)
                session.commit()
                session.refresh(bounty)

                verification_mode = (bounty.verification_mode or "auto").lower()
                test_cmd = bounty.test_command or "pytest"
                if verification_mode == "auto":
                    sb = get_sandbox(request)
                    if sb is None:
                        raise HTTPException(status_code=503, detail="Sandbox is disabled")
                    logger.info("[automation] Running validation for Bounty %s: %s", bounty.id, test_cmd)
                    try:
                        async with execution_semaphore:
                             # Use a temporary worktree cloned from the bare repo for running tests
                             work_dir = tempfile.mkdtemp(prefix="agenthub_auto_verify_")
                             try:
                                 subprocess.run(["git", "clone", bare_repo_path, work_dir], check=True, capture_output=True)
                                 v_exit_code, v_stdout = sb.run_tests(work_dir, test_cmd)
                             finally:
                                 shutil.rmtree(work_dir, ignore_errors=True)
                    except Exception as e:
                        v_exit_code, v_stdout = -1, f"Execution failed under semaphore: {str(e)}"
                elif verification_mode == "human":
                    v_exit_code, v_stdout = None, "Human verification required"
                elif verification_mode == "external":
                    v_exit_code, v_stdout = None, "External CI verification required"
                else:
                    v_exit_code, v_stdout = -1, f"Unknown verification_mode: {verification_mode}"

        # Save record to history
        try:
            # Capture SHA
            sha_result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=work_dir, capture_output=True, text=True)
            sha = sha_result.stdout.strip() if sha_result.returncode == 0 else None

            # [Blind-Spot 1] Human-in-the-loop: status='pending'
            record = CommitRecord(
                repo_name=repo_name,
                commit_sha=sha,
                agent_id=req.agent_id,
                bounty_id=req.bounty_id,
                branch_name=branch_name if 'branch_name' in locals() else None,
                status="pending",
                model_name=req.model_name,
                intent_category=req.intent_category,
                intent_description=req.intent_description,
                diff_summary=req.diff_summary,
                trace_json=trace_commit,
                verification_exit_code=v_exit_code,
                verification_stdout=v_stdout[:5000] if v_stdout else None
            )
            session.add(record)
            session.commit()
        except Exception as db_err:
            logger.error("Failed to record commit history: %s", db_err)

        # Sync task tree to repository after submission
        try:
            tree_service = GitTreeService(session, STORE_ROOT)
            tree_service.sync_repo_task_tree(repo_name, trusted_agent_id)
        except Exception as e:
            logger.warning("Failed to sync task tree after commit: %s", e)

        return {
            "success": True,
            "repo": repo_name,
            "files_committed": list(req.files.keys()),
            "agent": req.agent_id,
            "sha": sha if 'sha' in locals() else None,
            "verification": {
                "exit_code": v_exit_code,
                "passed": v_exit_code == 0 if v_exit_code is not None else None
            }
        }

    except subprocess.CalledProcessError as e:
        # Avoid leaking raw stderr to clients
        err_msg = e.stderr.decode(errors="replace")[:2000] if getattr(e, "stderr", None) else str(e)
        logger.error("[commit] git operation failed: %s", err_msg)
        return {"success": False, "error": "Git operation failed"}
    except HTTPException:
        raise  # Re-raise HTTP exceptions (403, 404, etc.)
    except Exception as e:
        logger.exception("[commit] unexpected error: %s: %s", type(e).__name__, e)
        raise HTTPException(status_code=500, detail=f"Internal error: {str(e)}")
    finally:
        # Cleanup
        shutil.rmtree(work_dir, ignore_errors=True)


# --- Review & Human-in-the-loop ---

@app.get("/api/v1/commits/pending")
def list_pending_submissions(
    session: Session = Depends(get_session),
    identity: Any = Depends(require_active_identity)
):
    """
    List submissions awaiting human approval.
    Requires either agent or human user authentication.
    """
    return session.exec(select(CommitRecord).where(CommitRecord.status == "pending")).all()

@app.get("/api/v1/commits/{commit_id}")
def get_commit_detail(commit_id: int, session: Session = Depends(get_session), identity: Any = Depends(require_active_identity)):
    """Fetch a single commit record and its git diff (minimal PR view)."""
    record = session.get(CommitRecord, commit_id)
    if not record:
        raise HTTPException(status_code=404, detail="Commit record not found")

    diff_text = None
    if record.commit_sha:
        try:
            repo_path = get_secure_repo_path(record.repo_name)
            diff_text = subprocess.check_output(
                ["git", "show", "--no-color", record.commit_sha],
                cwd=repo_path,
                stderr=subprocess.STDOUT
            ).decode("utf-8", errors="replace")
            diff_text = diff_text[:20000]
        except subprocess.CalledProcessError:
            diff_text = None

    return {
        "record": record,
        "diff": diff_text
    }

@app.get("/api/v1/commits/pending/verification")
def list_pending_verifications(repo_name: Optional[str] = None, session: Session = Depends(get_session)):
    """List commits pending manual/external verification."""
    statement = select(CommitRecord, Bounty).where(CommitRecord.bounty_id == Bounty.id)
    statement = statement.where(CommitRecord.status == "pending")
    statement = statement.where(Bounty.verification_mode.in_(["human", "external"]))
    if repo_name:
        statement = statement.where(CommitRecord.repo_name == repo_name)
    rows = session.exec(statement).all()
    results = []
    for record, bounty in rows:
        results.append({
            "commit_id": record.id,
            "repo_name": record.repo_name,
            "bounty_id": record.bounty_id,
            "verification_mode": bounty.verification_mode,
            "verification_exit_code": record.verification_exit_code,
            "verification_stdout": record.verification_stdout,
            "diff_summary": record.diff_summary,
            "agent_id": record.agent_id,
        })
    return results

@app.post("/api/v1/commits/{commit_id}/blackbox-test")
def submit_blackbox_test(commit_id: int, report: BlackboxReport, session: Session = Depends(get_session), identity: Any = Depends(require_active_identity)):
    """Submit a blackbox test report for a commit."""
    # If identity is an Agent, check role
    if hasattr(identity, "role"):
        if identity.role.lower() != "tester":
            raise HTTPException(status_code=403, detail="Forbidden: only tester can submit blackbox reports")
    # If identity is a User, we allow it (admin action)

    record = session.get(CommitRecord, commit_id)
    if not record:
        raise HTTPException(status_code=404, detail="Commit record not found")

    record.blackbox_report = report.model_dump()
    record.blackbox_status = "passed" if report.overall_verdict.upper() == "PASS" else "failed"

    # Auto-extract memory from test report for the agent
    agent_id = getattr(identity, "agent_id", None)
    if not agent_id and hasattr(identity, "id"): # Fallback to database ID if agent_id string is missing
        agent_id = f"agent-{identity.id}"

    if agent_id:
        passed_count = sum(1 for r in report.results if r.passed)
        memory_content = (
            f"Blackbox test performed on {record.repo_name} (Verdict: {record.blackbox_status.upper()}). "
            f"Tested endpoint: {report.endpoint}. Success: {passed_count}/{len(report.results)}. "
        )
        failed_tests = [f"{r.method} {r.api_path}" for r in report.results if not r.passed]
        if failed_tests:
            memory_content += f"Failed patterns: {', '.join(failed_tests)}."

        try:
            memory_service.add_memory(agent_id, memory_content, metadata={
                "commit_id": commit_id,
                "repo": record.repo_name,
                "role": "tester"
            })
        except Exception as e:
            logger.warning("Failed to store memory from report: %s", e)

    # Do NOT auto-approve or merge on blackbox PASS; require reviewer approval
    if record.blackbox_status == "passed":
        record.status = "pending"
    else:
        record.status = "rejected"
        if record.bounty_id:
            bounty = session.get(Bounty, record.bounty_id)
            if bounty and bounty.assignee == record.agent_id:
                from agent_auth.services.bounty_fsm import transition
                from persistence import BountyStatus
                updated, err = transition(session, bounty.id, BountyStatus.IN_PROGRESS.value, ctx={"actor_type": "system"})
                if err:
                    logger.warning("FSM revert failed: %s", err)
                else:
                    # 黑盒失败/拒绝：回退一次步骤计数，避免无效重试长期锁死
                    try:
                        fresh = session.get(Bounty, record.bounty_id)
                        if fresh:
                            fresh.current_steps = max(0, (fresh.current_steps or 0) - 1)
                            session.add(fresh)
                    except Exception as _e:
                        logger.warning("Failed to decrement current_steps on reject: %s", _e)

    session.add(record)
    session.commit()
    return {"message": f"Blackbox test submitted. Status: {record.blackbox_status}", "commit_id": commit_id}

@app.post("/api/v1/commits/{commit_id}/verify")
def verify_commit(commit_id: int, req: VerificationRequest, session: Session = Depends(get_session), agent: Agent = Depends(require_agent)):
    """Manual verification from executor/reviewer agents."""
    if agent.role.lower() != "executor":
        raise HTTPException(status_code=403, detail="Only executor can verify")
    record = session.get(CommitRecord, commit_id)
    if not record:
        raise HTTPException(status_code=404, detail="Commit record not found")
    if record.status not in {"pending", "conflict"}:
        raise HTTPException(status_code=409, detail="Commit not in a verifiable state")

    record.verification_exit_code = req.exit_code
    record.verification_stdout = (req.stdout or "")[:5000] if req.stdout is not None else None
    session.add(record)
    session.commit()
    return {"message": "Verification recorded", "commit_id": commit_id}

@app.post("/api/v1/commits/{commit_id}/verify/external")
async def verify_commit_external(commit_id: int, request: Request, req: VerificationRequest, x_ci_token: str = Header(None, alias="X-CI-Token"), x_ci_signature: str = Header(None, alias="X-CI-Signature"), session: Session = Depends(get_session)):
    """External CI callback verification."""
    expected_token = os.getenv("EXTERNAL_CI_TOKEN")
    expected_secret = os.getenv("EXTERNAL_CI_SECRET")
    if expected_secret:
        body_bytes = await request.body()
        import hmac
        import hashlib
        computed = hmac.new(expected_secret.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()
        if not x_ci_signature or not hmac.compare_digest(x_ci_signature, computed):
            raise HTTPException(status_code=401, detail="Invalid CI signature")
    else:
        if not expected_token or not x_ci_token or x_ci_token != expected_token:
            raise HTTPException(status_code=401, detail="Invalid CI token")
    record = session.get(CommitRecord, commit_id)
    if not record:
        raise HTTPException(status_code=404, detail="Commit record not found")
    if record.status not in {"pending", "conflict"}:
        raise HTTPException(status_code=409, detail="Commit not in a verifiable state")

    record.verification_exit_code = req.exit_code
    record.verification_stdout = (req.stdout or "")[:5000] if req.stdout is not None else None
    session.add(record)
    session.commit()
    return {"message": "External verification recorded", "commit_id": commit_id}

# --- Leaderboard & Stats ---

@app.get("/api/v1/stats/leaderboard")
def get_leaderboard(session: Session = Depends(get_session)):
    """[Blind-Spot 5] Leaderboard based on success rate."""
    records = session.exec(select(CommitRecord)).all()
    stats = {}
    for r in records:
        if r.agent_id not in stats:
            stats[r.agent_id] = {"total": 0, "success": 0}
        stats[r.agent_id]["total"] += 1
        if r.status == "approved":
            stats[r.agent_id]["success"] += 1

    leaderboard = []
    for aid, data in stats.items():
        rate = (data["success"] / data["total"]) * 100
        leaderboard.append({
            "agent_id": aid,
            "success_rate": f"{rate:.1f}%",
            "total_commits": data["total"],
            "rank": "Gold 🦞" if rate > 90 else "Silver 🦞"
        })

    return sorted(leaderboard, key=lambda x: float(x["success_rate"].replace('%','')), reverse=True)

# --- App Factory ---

def create_app() -> FastAPI:
    # Use the already-created global app (routes are bound via decorators above)
    global app

    # rate limit
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    # middleware
    app.middleware("http")(limit_request_size_mw)

    # static
    if os.path.exists(STATIC_DIR):
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    # CORS
    frontend_url = os.getenv("FRONTEND_URL", "http://localhost:3000")
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

    return app

# Backward-compatible module-level app
app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
