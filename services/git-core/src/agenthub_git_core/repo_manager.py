import os
import subprocess
import shutil
import stat
import sys
from pathlib import Path

# --- Hack for Monorepo Paths ---
base_dir = os.path.dirname(os.path.abspath(__file__))
protocol_path = os.path.abspath(os.path.join(base_dir, "../../../../packages/protocol/src"))
if protocol_path not in sys.path:
    sys.path.append(protocol_path)

from agenthub_protocol.path_utils import ensure_safe_path

class RepoManager:
    def __init__(self, storage_root: str):
        self.storage_root = os.path.abspath(storage_root)
        os.makedirs(self.storage_root, exist_ok=True)
    
    def create_repo(self, repo_name: str) -> str:
        """Initialize a bare git repo and install the AgentHub hook."""
        try:
            repo_path_obj = ensure_safe_path(self.storage_root, repo_name, "Invalid repository name")
            repo_path = str(repo_path_obj)
        except ValueError as e:
            raise ValueError(str(e))
        # Enforce simple naming: single level and .git suffix
        if "/" in repo_name or "\\" in repo_name:
            raise ValueError("Invalid repository name: nested paths are not allowed")
        if not repo_name.endswith(".git"):
            raise ValueError("Invalid repository name: must end with .git")
            
        if os.path.exists(repo_path):
            raise ValueError("Repository already exists")
            
        subprocess.run(["git", "init", "--bare", repo_path], check=True, capture_output=True)
        try:
            self.install_hook(repo_path)
        except Exception as e:
            shutil.rmtree(repo_path, ignore_errors=True)
            raise RuntimeError(f"Failed to install hook: {e}")
        return repo_path
    
    def install_hook(self, repo_path: str):
        """Symlink or write the hook script."""
        hook_path = os.path.join(repo_path, "hooks", "pre-receive")
        
        # We need to find the absolute path to our hook_logic.py
        # Use dynamic path relative to this file
        base_dir = os.path.dirname(os.path.abspath(__file__))
        script_path = os.path.join(base_dir, "hook_logic.py")
        
        # Use sys.executable to ensure we use the same virtualenv
        python_executable = sys.executable
        
        hook_content = f"""#!/bin/sh
# AgentHub Hook Wrapper
# Using verified python path: {python_executable}
"{python_executable}" "{script_path}"
"""
        with open(hook_path, "w") as f:
            f.write(hook_content)
        
        st = os.stat(hook_path)
        os.chmod(hook_path, st.st_mode | stat.S_IEXEC)
        print(f"🪝 Hook installed at {hook_path}")

if __name__ == "__main__":
    # Test creation
    # Ensure current dir is writable or use a temp dir
    mgr = RepoManager("./temp_git_store")
    repo = mgr.create_repo("test-project.git")
    print(f"Unit Test Repo Created: {repo}")
