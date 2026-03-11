import os
import time
import tarfile
import io
from typing import Tuple, Optional
from e2b_code_interpreter import Sandbox as E2BCodeSandbox
from .sandbox import Sandbox

class E2BSandbox(Sandbox):
    """
    Secure Cloud Sandbox using E2B.
    Provides isolated micro-VMs for executing untrusted Agent code.
    Supports both one-off test runs and persistent sessions.
    """
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("E2B_API_KEY")
        self.cpu_count = int(os.getenv("SANDBOX_CPU_COUNT", "2"))
        self.memory_mb = int(os.getenv("SANDBOX_MEMORY_MB", "512"))
        self.timeout_sec = int(os.getenv("SANDBOX_TIMEOUT", "300"))
        
        if not self.api_key:
            print("⚠️ E2B_API_KEY not found. E2BSandbox will fail if initialized.")

    def run_tests(self, repo_path: str, test_command: str, timeout: int = 120) -> Tuple[int, str]:
        """Backward compatibility: Runs a complete lifecycle in one call."""
        if not self.api_key:
            return -1, "❌ E2B_API_KEY is missing."
        
        try:
            with E2BCodeSandbox.create(timeout=self.timeout_sec) as sbx:
                sbx_work_dir = "/home/user/repo"
                self._upload_repo(sbx, repo_path, sbx_work_dir)
                return self._execute_run(sbx, sbx_work_dir, test_command, timeout)
        except Exception as e:
            return -1, f"❌ E2B Error: {str(e)}"

    def create_session(self, repo_path: Optional[str] = None) -> str:
        """Starts a persistent sandbox session and returns its ID."""
        sbx = E2BCodeSandbox.create(timeout=self.timeout_sec)
        if repo_path:
             sbx_work_dir = "/home/user/repo"
             self._upload_repo(sbx, repo_path, sbx_work_dir)
             # Optional: pre-install deps
             try:
                 sbx.commands.run(f"pip install -r {sbx_work_dir}/requirements.txt", cwd=sbx_work_dir, timeout=180)
             except Exception:
                 pass
        return sbx.sandbox_id

    def run_command(self, session_id: str, command: str, cwd: str = "/home/user/repo") -> Tuple[int, str]:
        """Runs a command on an existing session."""
        sbx = E2BCodeSandbox.connect(session_id)
        proc = sbx.commands.run(command, cwd=cwd, timeout=self.timeout_sec)
        return proc.exit_code, proc.stdout + proc.stderr

    def close_session(self, session_id: str):
        """Terminates a persistent session."""
        sbx = E2BCodeSandbox.connect(session_id)
        # E2B sessions might stay alive until timeout if not explicitly killed
        sbx.kill()

    def _upload_repo(self, sbx: E2BCodeSandbox, repo_path: str, sbx_work_dir: str):
        tar_stream = io.BytesIO()
        with tarfile.open(fileobj=tar_stream, mode='w:gz') as tar:
            tar.add(repo_path, arcname=".")
        tar_stream.seek(0)
        tar_bytes = tar_stream.read()
        remote_tar = "/home/user/repo.tar.gz"
        sbx.files.write(remote_tar, tar_bytes)
        sbx.commands.run(f"mkdir -p {sbx_work_dir}")
        sbx.commands.run(f"tar -xzf {remote_tar} -C {sbx_work_dir}")

    def _execute_run(self, sbx: E2BCodeSandbox, sbx_work_dir: str, command: str, timeout: int) -> Tuple[int, str]:
        proc = sbx.commands.run(command, cwd=sbx_work_dir, timeout=timeout)
        return proc.exit_code, proc.stdout + proc.stderr
