import sys
import os
from pathlib import Path
import re
import subprocess
import tempfile
import shutil
import json
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Body, Depends, Request, Header, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
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
from agenthub_execution_vmm.e2b_sandbox import E2BSandbox
from agenthub_execution_vmm.guard import ExecutionGuard
from agenthub_protocol.path_utils import ensure_safe_path
from agenthub_protocol.validator import TraceValidator
from agent_auth import agent_router, claim_router, wechat_router
from agent_auth.database import get_db as get_auth_session, get_engine as get_auth_engine
from agent_auth.models import Agent, AgentStatus
from agent_auth.services import start_scheduler, stop_scheduler
from agent_auth.utils import verify_api_key, get_api_key_prefix, get_legacy_api_key_prefix, is_valid_api_key_format
from persistence import Bounty, CommitRecord, get_session, create_db_and_tables

# --- Execution & Cost Guards ---
# [Blind-Spot 2] Global Concurrency Limit
MAX_CONCURRENT_RUNS = int(os.getenv("MAX_CONCURRENT_RUNS", "3"))
execution_semaphore = asyncio.Semaphore(MAX_CONCURRENT_RUNS)

from datetime import date

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
            return True
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
app = FastAPI(title="AgentHub API", version="0.1.0")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# --- Request Size Limit (20MB) ---
MAX_REQUEST_SIZE = 20 * 1024 * 1024  # 20MB

@app.middleware("http")
async def limit_request_size(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > MAX_REQUEST_SIZE:
        return JSONResponse(
            status_code=413,
            content={"detail": f"Payload too large. Maximum allowed size is {MAX_REQUEST_SIZE / 1024 / 1024}MB."}
        )
    return await call_next(request)

# Serve static files
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
PROMPT_DIR = os.path.join(os.path.dirname(__file__), "prompts")
if os.path.exists(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# --- CORS ---
# In production, set this to your actual frontend domain
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_URL],
    allow_origin_regex=r"http://localhost:.*",  # Support any localhost port for development
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Services Singleton ---
STORE_ROOT = os.path.abspath("./agenthub_data/repos")

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

# Ensure dirs exist
if not os.path.exists(STORE_ROOT):
    os.makedirs(STORE_ROOT)

# Initialize Databases
from agent_auth.database import create_db_and_tables as init_auth_db
create_db_and_tables() # Persistence DB
init_auth_db()         # Agent Auth DB

repo_manager = RepoManager(STORE_ROOT)
# [AI-Note] Decoupled: Indexer will self-disable if ZHIPU_API_KEY is missing.
indexer = VectorIndexer(collection_name="agenthub_prod", embedding_dim=1024)
if not indexer.embedder.client:
    print("ℹ️  [Core] Semantic Indexing is DISABLED (Missing ZHIPUAI_API_KEY). Running in Pure Git Mode.")
else:
    print("🧠 [Core] Semantic Indexing is ENABLED (Using Zhipu AI).")
parser = PythonASTParser()

# Memory Store removed, using SQLite via SQLModel

# --- Security Configuration & Sandbox Selection ---
# [AI-Note] APP_ENV='production' forces E2B Cloud Sandbox. SubprocessSandbox is for development ONLY.
APP_ENV = os.getenv("APP_ENV", "production").lower()
E2B_API_KEY = os.getenv("E2B_API_KEY")
ALLOW_INSECURE_SANDBOX = os.getenv("ALLOW_INSECURE_SANDBOX", "0") == "1"

if APP_ENV == "production":
    if not E2B_API_KEY:
        print("❌ [CRITICAL] E2B_API_KEY is missing in PRODUCTION environment.")
        print("❌ Security policy prohibits local SubprocessSandbox in production. System exit.")
        sys.exit(1)
    print("🔐 [Core] PROD MODE: Initializing Secure Cloud Sandbox (E2B)...")
    sandbox = E2BSandbox()
else:
    # Development Mode Fallback
    if E2B_API_KEY:
        print("🔐 [Core] DEV MODE: E2B_API_KEY detected. Using Secure Cloud Sandbox.")
        sandbox = E2BSandbox()
    else:
        if not ALLOW_INSECURE_SANDBOX:
            raise RuntimeError("E2B_API_KEY missing and ALLOW_INSECURE_SANDBOX=0. Refusing to use SubprocessSandbox.")
        print("⚠️ [Core] DEV MODE: E2B_API_KEY not found. Fallback to INSECURE SubprocessSandbox.")
        print("⚠️ [Security] This mode is strictly for local development and should not be used with untrusted code.")
        sandbox = SubprocessSandbox()

# --- Models ---

class AgentIdentity(BaseModel):
    agent_id: str
    model_name: str

# Bounty model is now imported from persistence.py

class CreateRepoRequest(BaseModel):
    name: str

class SearchResponse(BaseModel):
    chunk_name: str
    code_snippet: str
    score: float

class SystemStats(BaseModel):
    active_agents: int
    total_repos: int
    total_vectors: int
    system_load: str

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
async def get_role_prompt(role_name: str, raw: bool = False):
    """Return the system prompt for a given role."""
    role = role_name.lower().strip()
    prompt_map = {
        "architect": "architect.md",
        "contributor": "contributor.md",
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
    return {"role": role, "prompt": prompt}

@app.get("/stats", response_model=SystemStats)
@limiter.limit("30/minute")
def get_stats(request: Request, auth_session: Session = Depends(get_auth_session)):
    """Returns real-time system statistics (no mock values)."""
    repos = [d for d in os.listdir(STORE_ROOT) if not d.startswith('.')]

    active_agents = auth_session.exec(
        select(Agent).where(Agent.status == AgentStatus.CLAIMED)
    ).all()

    total_vectors = 0
    if indexer.client:
        try:
            total_vectors = indexer.client.count(
                collection_name=indexer.collection_name,
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

@app.get("/repos")
@limiter.limit("30/minute")
def list_repos(request: Request):
    if not os.path.exists(STORE_ROOT):
        return []
    return [d for d in os.listdir(STORE_ROOT) if not d.startswith('.')]

@app.post("/repos")
@limiter.limit("10/minute")
def create_repo(request: Request, req: CreateRepoRequest, agent: Agent = Depends(require_agent)):
    """Creates a new AgentHub repository with Protocol Hooks."""
    # Security validation
    get_secure_repo_path(req.name)
    try:
        path = repo_manager.create_repo(req.name)
        return {"id": req.name, "path": path, "status": "created"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/index")
def index_code(repo_name: str, file_path: str, content: str = Body(..., media_type="text/plain"), agent: Agent = Depends(require_agent)):
    """
    Manually index code content. 
    """
    get_secure_repo_path(repo_name)
    chunks = parser.parse(content)
    for c in chunks:
        indexer.index_chunk(repo_name, file_path, c)
    return {"indexed_chunks": len(chunks)}

@app.get("/search", response_model=List[SearchResponse])
def search_code(query: str, repo_id: Optional[str] = None, limit: int = 3, offset: int = 0):
    """Semantic search for code chunks."""
    if limit < 1:
        raise HTTPException(status_code=400, detail="limit must be >= 1")
    if limit > 20:
        limit = 20
    if offset < 0:
        raise HTTPException(status_code=400, detail="offset must be >= 0")

    fetch_limit = min(limit + offset, 50)
    results = indexer.search(query, limit=fetch_limit, repo_id=repo_id)
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

    repo_path = get_secure_repo_path(repo_name)
    exit_code, output = sandbox.run_tests(repo_path, cmd)
    return {
        "repo": repo_name,
        "exit_code": exit_code,
        "passed": exit_code == 0,
        "logs": output[:1000]
    }

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

@app.get("/bounties")
@limiter.limit("60/minute")
def list_bounties(request: Request, session: Session = Depends(get_session)):
    """List all open bounties."""
    statement = select(Bounty)
    return session.exec(statement).all()

@app.post("/bounties")
@limiter.limit("20/minute")
def create_bounty(request: Request, bounty: Bounty, session: Session = Depends(get_session), agent: Agent = Depends(require_agent)):
    """Post a new job."""
    if not bounty.verification_mode:
        bounty.verification_mode = os.getenv("DEFAULT_VERIFICATION_MODE", "auto")
    if bounty.verification_mode and bounty.verification_mode.lower() not in {"auto", "human", "external"}:
        raise HTTPException(status_code=400, detail="Invalid verification_mode")
    session.add(bounty)
    session.commit()
    session.refresh(bounty)
    return bounty

@app.post("/bounties/{parent_id}/decompose")
def decompose_task(parent_id: str, sub_tasks: List[Bounty], agent_id: str, session: Session = Depends(get_session), auth_session: Session = Depends(get_auth_session), agent: Agent = Depends(require_agent)):
    """[Task Board] Allow Architect agents to split a task into atomic sub-tasks."""
    if str(agent.id) != agent_id:
        raise HTTPException(status_code=403, detail="Agent ID mismatch")
    parent = session.get(Bounty, parent_id)
    if not parent:
        raise HTTPException(status_code=404, detail="Parent task not found")
    
    # Verify Architect Role
    agent = auth_session.exec(select(Agent).where(Agent.id == agent_id)).first()
    if not agent or agent.role.lower() != "architect":
        raise HTTPException(status_code=403, detail="Forbidden: Only Architect agents can decompose tasks.")

    created_tasks = []
    for st in sub_tasks:
        st.parent_id = parent_id
        st.status = "open"
        st.repo_name = parent.repo_name # Inherit repo
        session.add(st)
        created_tasks.append(st)
    
    session.commit()
    for t in created_tasks:
        session.refresh(t)
    return {"parent_id": parent_id, "children": created_tasks}

@app.post("/bounties/{bounty_id}/claim")
def claim_bounty_route(bounty_id: str, agent_id: str, session: Session = Depends(get_session), auth_session: Session = Depends(get_auth_session), agent: Agent = Depends(require_agent)):
    """Agent claims a job."""
    if str(agent.id) != agent_id:
        raise HTTPException(status_code=403, detail="Agent ID mismatch")
    return claim_bounty(bounty_id=bounty_id, agent_id=agent_id, session=session, auth_session=auth_session)
def claim_bounty(bounty_id: str, agent_id: str, session: Session = Depends(get_session), auth_session: Session = Depends(get_auth_session)):
    """Agent claims a job."""
    bounty = session.get(Bounty, bounty_id)
    if not bounty:
        raise HTTPException(status_code=404, detail="Bounty not found")
        
    # [Task Board] Pessimistic Locking
    if bounty.status != "open":
        raise HTTPException(status_code=409, detail=f"Conflict: Task already claimed by Agent {bounty.assignee}")
        
    # Verify Agent exists and Check Role Separation
    agent = auth_session.exec(select(Agent).where(Agent.id == agent_id)).first()
    if not agent:
        # Fallback for UUID search if agent_id is string
        try:
            from uuid import UUID
            agent = auth_session.get(Agent, UUID(agent_id))
        except:
            raise HTTPException(status_code=404, detail="Agent not found in registry")

    if not agent:
        raise HTTPException(status_code=404, detail="Agent identity not found")

    if agent.role.lower() != bounty.required_role.lower():
        raise HTTPException(status_code=403, detail=f"Role Mismatch: This task requires Architect/Contributor/Reviewer: {bounty.required_role}")

    bounty.status = "claimed"
    bounty.assignee = agent_id
    session.add(bounty)
    session.commit()
    session.refresh(bounty)
    return bounty

# --- API-Based Git Operations ---

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
            return {"success": False, "error": result.stderr}
        
        # --- Automated Verification (P1 MVP) ---
        v_exit_code = None
        v_stdout = None
        
        if req.bounty_id:
            bounty = session.get(Bounty, req.bounty_id)
            if bounty:
                # [Task Board] Ownership Verification
                if bounty.assignee and bounty.assignee != trusted_agent_id:
                    raise HTTPException(status_code=403, detail=f"Forbidden: Task {req.bounty_id} is locked by Agent {bounty.assignee}")

                # [Blind-Spot 2] Cost Control: Max Steps
                if bounty.current_steps >= bounty.max_steps:
                    raise HTTPException(status_code=403, detail=f"Bounty {req.bounty_id} has exceeded the execution step limit ({bounty.max_steps}).")
                
                # [Blind-Spot 2] Rough Cost Check
                est_cost = ExecutionGuard.estimate_cost(is_new_session=True)
                if not budget_tracker.check_and_record(est_cost):
                    raise HTTPException(status_code=402, detail="Daily platform budget exceeded. Try again tomorrow.")

                bounty.current_steps += 1
                session.add(bounty)

                verification_mode = (bounty.verification_mode or "auto").lower()
                test_cmd = bounty.test_command or "pytest"
                if verification_mode == "auto":
                    print(f"🛠️ [Automation] Running validation for Bounty {bounty.id}: {test_cmd}")
                    try:
                        async with execution_semaphore:
                             v_exit_code, v_stdout = sandbox.run_tests(bare_repo_path, test_cmd)
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
            print(f"Failed to record commit history: {db_err}")

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
        return {"success": False, "error": str(e)}
    finally:
        # Cleanup
        shutil.rmtree(work_dir, ignore_errors=True)


# --- Review & Human-in-the-loop ---

@app.get("/api/v1/commits/pending")
def list_pending_submissions(session: Session = Depends(get_session), agent: Agent = Depends(require_agent)):
    """[Blind-Spot 1] List submissions awaiting human approval."""
    return session.exec(select(CommitRecord).where(CommitRecord.status == "pending")).all()

@app.get("/api/v1/commits/{commit_id}")
def get_commit_detail(commit_id: int, session: Session = Depends(get_session), agent: Agent = Depends(require_agent)):
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

@app.post("/api/v1/commits/{commit_id}/approve")
def approve_commit(commit_id: int, session: Session = Depends(get_session), agent: Agent = Depends(require_agent)):
    """Approve an agent's submission and 'merge' it."""
    record = session.get(CommitRecord, commit_id)
    if not record:
        raise HTTPException(status_code=404, detail="Commit record not found")
    if agent.role.lower() not in {"architect", "reviewer", "executor"}:
        raise HTTPException(status_code=403, detail="Forbidden: insufficient role to approve commits")
    if str(agent.id) == str(record.agent_id):
        raise HTTPException(status_code=403, detail="Forbidden: cannot approve own commit")
    
    record.status = "approved"
    session.add(record)

    # Fast-forward main to the approved branch head
    if record.branch_name:
        repo_path = get_secure_repo_path(record.repo_name)
        ref_name = f"refs/heads/{record.branch_name}"
        try:
            # If main exists, require fast-forward only
            main_ref = "refs/heads/main"
            main_exists = subprocess.run(["git", "show-ref", "--verify", "--quiet", main_ref], cwd=repo_path).returncode == 0
            if main_exists:
                ff_check = subprocess.run(
                    ["git", "merge-base", "--is-ancestor", main_ref, ref_name],
                    cwd=repo_path
                )
                if ff_check.returncode != 0:
                    record.status = "conflict"
                    session.add(record)
                    session.commit()
                    raise HTTPException(status_code=409, detail="Non-fast-forward merge detected; manual review required.")

            sha = subprocess.check_output(["git", "rev-parse", ref_name], cwd=repo_path).decode().strip()
            subprocess.run(["git", "update-ref", "refs/heads/main", sha], cwd=repo_path, check=True)
        except subprocess.CalledProcessError as e:
            raise HTTPException(status_code=500, detail=f"Failed to update main: {e}")
    
    if record.bounty_id:
        bounty = session.get(Bounty, record.bounty_id)
        if bounty:
            bounty.status = "completed"
            session.add(bounty)
            
    session.commit()
    return {"message": f"Commit {commit_id} approved."}

@app.post("/api/v1/commits/{commit_id}/reject")
def reject_commit(commit_id: int, session: Session = Depends(get_session), agent: Agent = Depends(require_agent)):
    """Reject an agent's submission."""
    record = session.get(CommitRecord, commit_id)
    if not record:
        raise HTTPException(status_code=404, detail="Commit record not found")
    if agent.role.lower() not in {"architect", "reviewer", "executor"}:
        raise HTTPException(status_code=403, detail="Forbidden: insufficient role to reject commits")
    if str(agent.id) == str(record.agent_id):
        raise HTTPException(status_code=403, detail="Forbidden: cannot reject own commit")
    
    record.status = "rejected"
    session.add(record)
    session.commit()
    return {"message": f"Commit {commit_id} rejected.", "branch": record.branch_name}

@app.post("/api/v1/commits/{commit_id}/verify")
def verify_commit(commit_id: int, req: VerificationRequest, session: Session = Depends(get_session), agent: Agent = Depends(require_agent)):
    """Manual verification from executor/reviewer agents."""
    if agent.role.lower() not in {"executor", "reviewer"}:
        raise HTTPException(status_code=403, detail="Only executor/reviewer can verify")
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
        import hmac, hashlib
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

# --- Router Registration ---
app.include_router(agent_router)
app.include_router(claim_router)
app.include_router(wechat_router)

@app.on_event("startup")
def start_background_jobs():
    from sqlmodel import Session as AuthSession
    def session_factory():
        return AuthSession(get_auth_engine())
    start_scheduler(session_factory)

@app.on_event("shutdown")
def stop_background_jobs():
    stop_scheduler()

if __name__ == "__main__":
    import uvicorn
    # Create DB tables on startup
    print("🚀 Initializing databases with WAL mode...")
    from persistence import create_db_and_tables
    # Since agent_auth.database has the same function name, import it locally
    from agent_auth.database import create_db_and_tables as create_auth_tables
    create_db_and_tables()
    create_auth_tables()
    
    uvicorn.run(app, host="0.0.0.0", port=8000)
