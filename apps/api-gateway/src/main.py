import sys
import os
from pathlib import Path
import random
import subprocess
import tempfile
import shutil
import json
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Body, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

# --- Hack for Monorepo Paths (MVP only) ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(os.path.abspath(os.path.join(BASE_DIR, "../../../packages/protocol/src")))
sys.path.append(os.path.abspath(os.path.join(BASE_DIR, "../../../services/git-core/src")))
sys.path.append(os.path.abspath(os.path.join(BASE_DIR, "../../../services/semantic-store/src")))
sys.path.append(os.path.abspath(os.path.join(BASE_DIR, "../../../services/execution-vmm/src")))

from agenthub_git_core.repo_manager import RepoManager
from agenthub_semantic_store.indexer import VectorIndexer
from agenthub_semantic_store.ast_parser import PythonASTParser
from agenthub_execution_vmm.sandbox import SubprocessSandbox
from agenthub_execution_vmm.e2b_sandbox import E2BSandbox
from agenthub_protocol.path_utils import ensure_safe_path
from agent_auth import agent_router, claim_router, wechat_router
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
VECTOR_DB_PATH = os.path.abspath("./agenthub_data/vectors.json")

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

# Ensure dirs exist
if not os.path.exists(STORE_ROOT):
    os.makedirs(STORE_ROOT)

# Initialize Databases
from agent_auth.database import create_db_and_tables as init_auth_db
create_db_and_tables() # Persistence DB
init_auth_db()         # Agent Auth DB

repo_manager = RepoManager(STORE_ROOT)
# Updated to match the new Qdrant-based VectorIndexer signature
indexer = VectorIndexer(collection_name="agenthub_prod", embedding_dim=1024)
parser = PythonASTParser()

# Memory Store removed, using SQLite via SQLModel

# --- Security Configuration & Sandbox Selection ---
# [AI-Note] APP_ENV='production' forces E2B Cloud Sandbox. SubprocessSandbox is for development ONLY.
APP_ENV = os.getenv("APP_ENV", "development").lower()
E2B_API_KEY = os.getenv("E2B_API_KEY")

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

ALLOWED_TEST_COMMANDS = ["pytest", "python", "python3", "tox", "nose"]

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

@app.get("/stats", response_model=SystemStats)
@limiter.limit("30/minute")
def get_stats(request: Request):
    """Returns real-time system statistics."""
    # Count Repos
    repos = [d for d in os.listdir(STORE_ROOT) if not d.startswith('.')]
    
    # Count Vectors (Mock reading internal state)
    # in MVP we just guess or read file size
    vec_count = 0
    if os.path.exists(VECTOR_DB_PATH):
        vec_count = 1  # Simplified
        
    return SystemStats(
        active_agents=random.randint(1, 5), # Mock
        total_repos=len(repos),
        total_vectors=vec_count * 5 + len(repos) * 2, # Mock
        system_load=f"{random.randint(10, 40)}%"
    )

@app.get("/repos")
@limiter.limit("30/minute")
def list_repos(request: Request):
    if not os.path.exists(STORE_ROOT):
        return []
    return [d for d in os.listdir(STORE_ROOT) if not d.startswith('.')]

@app.post("/repos")
@limiter.limit("10/minute")
def create_repo(request: Request, req: CreateRepoRequest):
    """Creates a new AgentHub repository with Protocol Hooks."""
    # Security validation
    get_secure_repo_path(req.name)
    try:
        path = repo_manager.create_repo(req.name)
        return {"id": req.name, "path": path, "status": "created"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/index")
def index_code(repo_name: str, file_path: str, content: str = Body(..., media_type="text/plain")):
    """
    Manually index code content. 
    """
    get_secure_repo_path(repo_name)
    chunks = parser.parse(content)
    for c in chunks:
        indexer.index_chunk(repo_name, file_path, c)
    return {"indexed_chunks": len(chunks)}

@app.get("/search", response_model=List[SearchResponse])
def search_code(query: str):
    """Semantic search for code chunks."""
    results = indexer.search(query, limit=3)
    response = []
    for r in results:
        response.append(SearchResponse(
            chunk_name=r["chunk_name"],
            code_snippet=r["code_snippet"],
            score=0.99 
        ))
    return response

@app.post("/verify")
@limiter.limit("10/minute")
def verify_repo(request: Request, repo_name: str, cmd: str = "pytest"):
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
def create_bounty(request: Request, bounty: Bounty, session: Session = Depends(get_session)):
    """Post a new job."""
    session.add(bounty)
    session.commit()
    session.refresh(bounty)
    return bounty

@app.post("/bounties/{bounty_id}/claim")
def claim_bounty(bounty_id: str, agent_id: str, session: Session = Depends(get_session)):
    """Agent claims a job."""
    bounty = session.get(Bounty, bounty_id)
    if not bounty:
        raise HTTPException(status_code=404, detail="Bounty not found")
        
    if bounty.status != "open":
        raise HTTPException(status_code=400, detail="Bounty already claimed")
        
    bounty.status = "claimed"
    bounty.assignee = agent_id
    session.add(bounty)
    session.commit()
    session.refresh(bounty)
    return bounty

# --- API-Based Git Operations ---

@app.post("/repos/{repo_name}/commit")
@limiter.limit("10/minute")
def api_commit(request: Request, repo_name: str, req: CommitRequest, session: Session = Depends(get_session)):
    """
    Submit code via API (no git client needed).
    Creates files and commits to the bare repo.
    """
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
            full_path = os.path.join(work_dir, file_path)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "w") as f:
                f.write(content)
        
        # Stage all changes
        subprocess.run(["git", "add", "-A"], cwd=work_dir, check=True, capture_output=True)
        
        # Determine Branch Name (Level  isolation)
        if req.bounty_id:
            branch_name = f"agent/{req.agent_id}/bounty_{req.bounty_id}"
        else:
            ts = int(time.time())
            branch_name = f"agent/{req.agent_id}/dev_{ts}"
            
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
                "agent_id": req.agent_id,
                "model_name": req.model_name
            }
        }
        
        # Commit with TraceCommit JSON as message
        commit_msg = json.dumps(trace_commit)
        subprocess.run(
            ["git", "commit", "-m", commit_msg],
            cwd=work_dir, check=True, capture_output=True,
            env={**os.environ, "GIT_AUTHOR_NAME": req.agent_id, "GIT_AUTHOR_EMAIL": f"{req.agent_id}@agenthub.dev",
                 "GIT_COMMITTER_NAME": req.agent_id, "GIT_COMMITTER_EMAIL": f"{req.agent_id}@agenthub.dev"}
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
                test_cmd = bounty.test_command or "pytest"
                print(f"🛠️ [Automation] Running validation for Bounty {bounty.id}: {test_cmd}")
                v_exit_code, v_stdout = sandbox.run_tests(bare_repo_path, test_cmd)
        
        # Save record to history
        try:
            # Capture SHA
            sha_result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=work_dir, capture_output=True, text=True)
            sha = sha_result.stdout.strip() if sha_result.returncode == 0 else None
            
            record = CommitRecord(
                repo_name=repo_name,
                commit_sha=sha,
                agent_id=req.agent_id,
                bounty_id=req.bounty_id,
                branch_name=branch_name if 'branch_name' in locals() else None,
                model_name=req.model_name,
                intent_category=req.intent_category,
                intent_description=req.intent_description,
                diff_summary=req.diff_summary,
                trace_json=trace_commit, # JSON column handles dict
                verification_exit_code=v_exit_code,
                verification_stdout=v_stdout[:5000] # Limit size
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


# --- Router Registration ---
app.include_router(agent_router)
app.include_router(claim_router)
app.include_router(wechat_router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
