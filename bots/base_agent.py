import os
import time
import json
import requests
import subprocess
import shutil
import datetime
from typing import List, Dict, Optional

API_URL = "http://127.0.0.1:8000"
WORKSPACE_DIR = os.path.abspath("./agent_workspace")

class BaseAgent:
    def __init__(self, agent_id: str, role: str):
        self.agent_id = agent_id
        self.role = role
        self.model_name = "gpt-4-turbo"
        
        if not os.path.exists(WORKSPACE_DIR):
            os.makedirs(WORKSPACE_DIR)

    def log(self, msg: str, emoji: str = "🤖"):
        print(f"{emoji} [{self.role.upper()}]: {msg}")

    # --- API Wrappers ---

    def create_repo(self, name: str) -> Optional[str]:
        self.log(f"Creating repo '{name}'...", "🏗️")
        try:
            res = requests.post(f"{API_URL}/repos", json={"name": name})
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

    def trigger_verify(self, repo_name: str) -> Dict:
        self.log(f"Requesting verification for {repo_name}...", "🧪")
        res = requests.post(f"{API_URL}/verify", params={"repo_name": repo_name, "cmd": "pytest"})
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
        subprocess.check_call(["git", "add", "."], cwd=repo_dir)
        
        # 2. Trace Commit
        trace_json = json.dumps(message_data)
        subprocess.check_call(["git", "commit", "-m", trace_json], cwd=repo_dir, stdout=subprocess.DEVNULL)
        
        # 3. Push
        self.log("Pushing changes...", "🚀")
        try:
            subprocess.check_call(["git", "push", "origin", "HEAD"], cwd=repo_dir, stderr=subprocess.PIPE)
            self.log("Push Accepted.", "✅")
            return True
        except subprocess.CalledProcessError as e:
            self.log(f"Push Rejected! Hook output:\n{e.stderr.decode()}", "❌")
            return False

    def construct_trace(self, summary: str, reasoning: List[str], intent_desc: str) -> Dict:
        """Helper to build protocol-compliant JSON"""
        return {
            "diff_summary": summary,
            "reasoning_trace": reasoning,
            "rejected_alternatives": ["None considered"],
            "context_snapshot": {
                "file_paths": [],
                "mock_env_vars": {}
            },
            "intent": {
                "category": "feature",
                "confidence_score": 0.95,
                "description": intent_desc
            },
            "author": {
                "agent_id": self.agent_id,
                "model_name": self.model_name
            },
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()
        }
