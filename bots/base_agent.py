import os
import time
import json
import requests
import subprocess
import shutil
import datetime
from typing import List, Dict, Optional

API_URL = os.getenv("AGENTHUB_API_URL", "http://127.0.0.1:8000")
AGENT_API_KEY = os.getenv("AGENT_API_KEY")
GIT_REMOTE_BASE = os.getenv("AGENTHUB_GIT_REMOTE_BASE")
WORKSPACE_DIR = os.path.abspath("./agent_workspace")

import sys
# Add root to path so we can import 'skills'
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from skills.registry import SkillRegistry
from skills.library.file_ops import ReadFileSkill, WriteFileSkill

class BaseAgent:
    def __init__(self, agent_id: str, role: str):
        self.agent_id = agent_id
        self.role = role
        self.model_name = "gpt-4-turbo"
        
        # Skills System
        self.skills = SkillRegistry()
        self.load_default_skills()
        
        if not os.path.exists(WORKSPACE_DIR):
            os.makedirs(WORKSPACE_DIR)

    def load_default_skills(self):
        self.skills.register(ReadFileSkill(root_dir=WORKSPACE_DIR))
        self.skills.register(WriteFileSkill(root_dir=WORKSPACE_DIR))

    def use_skill(self, skill_name: str, **kwargs):
        skill = self.skills.get(skill_name)
        if not skill:
            return f"Error: Skill '{skill_name}' not found."
        try:
            return skill.validate_and_execute(**kwargs)
        except Exception as e:
            return f"Error executing skill '{skill_name}': {e}"

    def log(self, msg: str, emoji: str = "🤖"):
        print(f"{emoji} [{self.role.upper()}]: {msg}")

    # --- API Wrappers ---

    def create_repo(self, name: str) -> Optional[str]:
        self.log(f"Creating repo '{name}'...", "🏗️")
        try:
            headers = {"X-API-Key": AGENT_API_KEY} if AGENT_API_KEY else {}
            res = requests.post(f"{API_URL}/repos", json={"name": name}, headers=headers)
            if res.status_code == 200:
                data = res.json()
                self.log(f"Repo created at {data['path']}", "✅")
                return data['path'] # Remote path
            else:
                self.log(f"Failed to create repo: {res.text}", "❌")
                return None
        except Exception as e:
            self.log(f"API Error: {e}", "❌")
            return None

    def search_code(self, query: str) -> List[Dict]:
        try:
            res = requests.get(f"{API_URL}/search", params={"query": query})
            return res.json()
        except:
            return []

    def list_repos(self) -> List[str]:
        try:
            res = requests.get(f"{API_URL}/repos")
            if res.status_code == 200:
                return res.json()
            return []
        except Exception:
            return []

    def get_remote_path(self, repo_name: str) -> Optional[str]:
        if GIT_REMOTE_BASE:
            return f"{GIT_REMOTE_BASE.rstrip('/')}/{repo_name}"
        return None

    def trigger_verify(self, repo_name: str) -> Dict:
        self.log(f"Requesting verification for {repo_name}...", "🧪")
        headers = {"X-API-Key": AGENT_API_KEY} if AGENT_API_KEY else {}
        res = requests.post(f"{API_URL}/verify", params={"repo_name": repo_name, "cmd": "pytest"}, headers=headers)
        return res.json()

    # --- Git Helpers ---

    def clone_repo(self, remote_path: str, repo_name: str) -> str:
        local_path = os.path.join(WORKSPACE_DIR, repo_name.replace(".git", ""))
        if os.path.exists(local_path):
            shutil.rmtree(local_path)
        
        self.log(f"Cloning {repo_name}...", "⬇️")
        subprocess.check_call(["git", "clone", remote_path, local_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return local_path

    def commit_and_push(self, repo_dir: str, message_data: Dict):
        # 1. Add
        subprocess.run(["git", "add", "."], cwd=repo_dir, check=True)
        
        # 2. Trace Commit
        trace_json = json.dumps(message_data)
        subprocess.run(["git", "commit", "-m", trace_json], cwd=repo_dir, check=True, stdout=subprocess.DEVNULL)
        
        # 3. Push
        self.log("Pushing changes...", "🚀")
        try:
            # Use run to capture stderr
            result = subprocess.run(
                ["git", "push", "origin", "HEAD"], 
                cwd=repo_dir, 
                capture_output=True, 
                text=True, 
                check=True
            )
            self.log("Push Accepted.", "✅")
            return True
        except subprocess.CalledProcessError as e:
            self.log(f"Push Rejected! Hook output:\n{e.stderr}", "❌")
            return False

    def construct_trace(self, summary: str, reasoning: List[str], intent_desc: str) -> Dict:
        """Helper to build protocol-compliant JSON"""
        return {
            "diff_summary": summary,
            "reasoning_trace": reasoning,
            "rejected_alternatives": ["None considered"],
            "context_snapshot": {
                "file_paths": [],
                "doc_references": [],
                "env_vars_accessed": [],
                "library_versions": {}
            },
            "intent": {
                "description": intent_desc,
                "category": "feature"
            },
            "author": {
                "agent_id": self.agent_id,
                "model_name": self.model_name
            },
            "timestamp": datetime.datetime.utcnow().isoformat()
        }
