import os
import subprocess
import shutil
import stat

class RepoManager:
    def __init__(self, storage_root: str):
        self.storage_root = os.path.abspath(storage_root)
        os.makedirs(self.storage_root, exist_ok=True)
    
    def create_repo(self, repo_name: str) -> str:
        """Initialize a bare git repo and install the AgentHub hook."""
        repo_path = os.path.join(self.storage_root, repo_name)
        if os.path.exists(repo_path):
            shutil.rmtree(repo_path)
            
        subprocess.run(["git", "init", "--bare", repo_path], check=True, capture_output=True)
        
        self.install_hook(repo_path)
        return repo_path
    
    def install_hook(self, repo_path: str):
        """Symlink or write the hook script."""
        hook_path = os.path.join(repo_path, "hooks", "pre-receive")
        
        # In a real app, this wraps the python call.
        # For this MVP, we wire it directly to our python logic.
        
        # We need to find the absolute path to our hook_logic.py
        # Assuming we are running from the monorepo root
        script_path = os.path.abspath("services/git-core/src/agenthub_git_core/hook_logic.py")
        
        hook_content = f"""#!/bin/sh
# AgentHub Hook Wrapper
# Uses system python3 for now. In prod, use the venv python.
python3 {script_path}
"""
        with open(hook_path, "w") as f:
            f.write(hook_content)
        
        st = os.stat(hook_path)
        os.chmod(hook_path, st.st_mode | stat.S_IEXEC)
        print(f"🪝 Hook installed at {hook_path}")

if __name__ == "__main__":
    # Test creation
    mgr = RepoManager("./temp_git_store")
    repo = mgr.create_repo("test-project.git")
    print(f"Unit Test Repo Created: {repo}")
