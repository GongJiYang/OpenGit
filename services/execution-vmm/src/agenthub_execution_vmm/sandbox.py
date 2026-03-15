from abc import ABC, abstractmethod
from typing import Tuple, Optional
import subprocess
import os
import shlex

class Sandbox(ABC):
    """
    Abstract Interface for Code Execution Environments.
    Implementations could be Local, Docker, or Firecracker.
    """
    
    @abstractmethod
    def run_tests(self, repo_path: str, test_command: str) -> Tuple[int, str]:
        """Runs tests in the sandbox."""
        pass

    @abstractmethod
    def create_session(self, repo_path: Optional[str] = None) -> str:
        """Starts a persistent sandbox session and returns its ID."""
        pass

    @abstractmethod
    def run_command(self, session_id: str, command: str, cwd: str = "/home/user/repo") -> Tuple[int, str]:
        """Runs a command on a persistent session."""
        pass

    @abstractmethod
    def close_session(self, session_id: str):
        """Terminates a persistent session."""
        pass

from .guard import ExecutionGuard

class SubprocessSandbox(Sandbox):
    """
    MVP Sandbox that runs commands locally in a subprocess.
    ⚠️ SECURITY WARNING: This provides NO ISOLATION. 
    Malicious agents can harm the host system. Use only for trusted demos.
    """
    
    def run_tests(self, repo_path: str, test_command: str, timeout: int = 30) -> Tuple[int, str]:
        if not os.path.exists(repo_path):
            return -1, f"❌ Repo path does not exist: {repo_path}"
            
        try:
            # [Blind-Spot 2] Security Guard
            tokens = ExecutionGuard.verify_command(test_command)
            
            print(f"⚡ [Sandbox] Executing in {repo_path}: {tokens}")
            
            result = subprocess.run(
                tokens,
                cwd=repo_path,
                shell=False, # Secure: No shell expansion
                capture_output=True,
                text=True,
                timeout=timeout
            )
            
            output = f"STDOUT:\n{result.stdout}\n\nSTDERR:\n{result.stderr}"
            sanitized_out = ExecutionGuard.sanitize_output(output)
            return result.returncode, sanitized_out
        except subprocess.TimeoutExpired:
            return 124, "❌ Execution Timed Out"
        except ValueError as ve:
             return -1, f"❌ Security Guard: {str(ve)}"
        except Exception as e:
             return -1, f"❌ Runtime Error: {str(e)}"
    def create_session(self, repo_path: Optional[str] = None) -> str:
        """Local sessions just return the repo path as ID."""
        return repo_path or "local_global"

    def run_command(self, session_id: str, command: str, cwd: str = None) -> Tuple[int, str]:
        """Runs a command locally."""
        repo_path = session_id if os.path.exists(session_id) else os.getcwd()
        actual_cwd = cwd if cwd and os.path.exists(cwd) else repo_path
        return self.run_tests(actual_cwd, command)

    def close_session(self, session_id: str):
        """No-op for local."""
        pass
