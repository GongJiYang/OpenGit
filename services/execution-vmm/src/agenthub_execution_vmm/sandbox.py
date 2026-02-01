from abc import ABC, abstractmethod
from typing import Tuple, Optional
import subprocess
import os
import shlex

class Sandbox(ABC):
    """
    Abstract Interface for Code Execution Environments.
    Implementations could be Local, Docker, Firecracker, or E2B.
    """
    
    @abstractmethod
    def run_tests(self, repo_path: str, test_command: str) -> Tuple[int, str]:
        """
        Runs tests in the sandbox.
        Args:
            repo_path: Absolute path to the code on the host (or mounted volume).
            test_command: The command to run (e.g., "pytest").
            
        Returns:
            Tuple[exit_code, output_logs]
        """
        pass

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
            # We split the command securely but for shell usage we might need shell=True
            # For this MVP we will use shell=True to allow complex commands like "pip install ... && pytest"
            # But we set cwd to the repo path.
            
            print(f"⚡ [Sandbox] Executing in {repo_path}: {test_command}")
            
            result = subprocess.run(
                test_command,
                cwd=repo_path,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout
            )
            
            output = f"STDOUT:\n{result.stdout}\n\nSTDERR:\n{result.stderr}"
            return result.returncode, output
            
        except subprocess.TimeoutExpired:
            return 124, "❌ Execution Timed Out"
        except Exception as e:
            return 1, f"❌ Sandbox Error: {str(e)}"
