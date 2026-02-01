import sys
import os
import random
import subprocess
import tempfile
import shutil
import json
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

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
import uuid
# ... (rest of imports)

app = FastAPI(title="AgentHub API", version="0.1.0")

# Serve static files
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# --- CORS ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Services Singleton ---
STORE_ROOT = os.path.abspath("./agenthub_data/repos")
VECTOR_DB_PATH = os.path.abspath("./agenthub_data/vectors.json")

# Ensure dirs exist
if not os.path.exists(STORE_ROOT):
    os.makedirs(STORE_ROOT)

repo_manager = RepoManager(STORE_ROOT)
# Updated to match the new Qdrant-based VectorIndexer signature
indexer = VectorIndexer(collection_name="agenthub_prod", embedding_dim=1024)
parser = PythonASTParser()

# Memory Store for Bounties (MVP)
BOUNTIES = []

# Sandbox Selection (V2 Upgrade)
if os.getenv("E2B_API_KEY"):
    print("🔐 [Core] Initializing Secure Cloud Sandbox (E2B)...")
    sandbox = E2BSandbox()
else:
    print("⚠️ [Core] E2B_API_KEY not found. Fallback to Insecure SubprocessSandbox.")
    sandbox = SubprocessSandbox()

# --- Models ---

class AgentIdentity(BaseModel):
    agent_id: str
    model_name: str

class Bounty(BaseModel):
    id: Optional[str] = None
    title: str
    description: str
    reward: int
    status: str = "open" # open, claimed, completed
    repo_name: str
    required_role: str # architect, contributor, executor
    assignee: Optional[str] = None

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

# --- Routes ---

@app.get("/")
def read_root():
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
def get_stats():
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
def list_repos():
    if not os.path.exists(STORE_ROOT):
        return []
    return [d for d in os.listdir(STORE_ROOT) if not d.startswith('.')]

@app.post("/repos")
def create_repo(req: CreateRepoRequest):
    """Creates a new AgentHub repository with Protocol Hooks."""
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
def verify_repo(repo_name: str, cmd: str = "pytest"):
    """Trigger the Sandbox to run tests on a repo."""
    repo_path = os.path.join(STORE_ROOT, repo_name)
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
    repo_path = os.path.join(STORE_ROOT, repo_name)
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
    repo_path = os.path.join(STORE_ROOT, repo_name)
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
def list_bounties():
    """List all open bounties."""
    return BOUNTIES

@app.post("/bounties")
def create_bounty(bounty: Bounty):
    """Post a new job."""
    if not bounty.id:
        bounty.id = str(uuid.uuid4())[:8]
    BOUNTIES.append(bounty)
    return bounty

@app.post("/bounties/{bounty_id}/claim")
def claim_bounty(bounty_id: str, agent_id: str):
    """Agent claims a job."""
    for b in BOUNTIES:
        if b.id == bounty_id:
            if b.status != "open":
                raise HTTPException(status_code=400, detail="Bounty already claimed")
            b.status = "claimed"
            b.assignee = agent_id
            return b
    raise HTTPException(status_code=404, detail="Bounty not found")

# --- API-Based Git Operations ---

@app.post("/repos/{repo_name}/commit")
def api_commit(repo_name: str, req: CommitRequest):
    """
    Submit code via API (no git client needed).
    Creates files and commits to the bare repo.
    """
    bare_repo_path = os.path.join(STORE_ROOT, repo_name)
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
        
        # Push to bare repo
        result = subprocess.run(
            ["git", "push", "origin", "HEAD"],
            cwd=work_dir, capture_output=True, text=True
        )
        
        if result.returncode != 0:
            return {"success": False, "error": result.stderr}
        
        return {
            "success": True,
            "repo": repo_name,
            "files_committed": list(req.files.keys()),
            "agent": req.agent_id
        }
        
    except subprocess.CalledProcessError as e:
        return {"success": False, "error": str(e)}
    finally:
        # Cleanup
        shutil.rmtree(work_dir, ignore_errors=True)


