"""
Docker Executor for AgentHub Runner

Executes CI/CD jobs in isolated Docker containers.
"""

import asyncio
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from rich.console import Console

console = Console()


class DockerExecutor:
    """
    Executes jobs in Docker containers with isolation and resource limits.
    """

    def __init__(
        self,
        work_path: Path,
        default_timeout: int = 600,
        memory_limit: str = "2g",
        cpu_limit: float = 2.0
    ):
        self.work_path = work_path
        self.jobs_path = work_path / "jobs"
        self.jobs_path.mkdir(parents=True, exist_ok=True)
        self.default_timeout = default_timeout
        self.memory_limit = memory_limit
        self.cpu_limit = cpu_limit

    async def execute(self, job: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute a job in a Docker container.

        Args:
            job: Job assignment from platform

        Returns:
            Dict with exit_code, stdout, stderr, test_results
        """
        job_id = job["job_id"]
        code_url = job.get("code_url", "")
        code_branch = job.get("code_branch", "main")
        test_command = job.get("test_command", "echo 'No test command'")
        env_vars = job.get("env_vars", {})
        timeout = job.get("timeout_seconds", self.default_timeout)

        # Create job workspace
        job_dir = self.jobs_path / str(job_id)
        job_dir.mkdir(parents=True, exist_ok=True)

        console.print(f"[dim]Workspace: {job_dir}[/dim]")

        try:
            # Step 1: Clone repository
            if code_url:
                console.print(f"[dim]Cloning {code_url} (branch: {code_branch})...[/dim]")
                clone_result = await self._run_command(
                    f"git clone --depth 1 --branch {code_branch} {code_url} .",
                    cwd=job_dir,
                    timeout=60
                )
                if clone_result["exit_code"] != 0:
                    return {
                        "exit_code": 1,
                        "stdout": clone_result["stdout"],
                        "stderr": f"Git clone failed:\n{clone_result['stderr']}"
                    }

            # Step 2: Build environment variables
            env_str = " ".join(f"-e {k}={v}" for k, v in env_vars.items())

            # Step 3: Run in Docker
            console.print(f"[dim]Running: {test_command}[/dim]")

            # Use a standard CI image
            image = "python:3.11-slim"

            docker_cmd = f"""docker run --rm \
                --memory={self.memory_limit} \
                --cpus={self.cpu_limit} \
                --network none \
                -v {job_dir}:/workspace \
                -w /workspace \
                {env_str} \
                {image} \
                bash -c "{test_command}"
            """

            result = await self._run_command(
                docker_cmd,
                timeout=timeout,
                capture_output=True
            )

            # Step 4: Parse test results if available
            test_results = None
            result_file = job_dir / "test-results.json"
            if result_file.exists():
                try:
                    test_results = json.loads(result_file.read_text())
                except json.JSONDecodeError:
                    pass

            return {
                "exit_code": result["exit_code"],
                "stdout": result["stdout"],
                "stderr": result["stderr"],
                "test_results": test_results
            }

        except asyncio.TimeoutError:
            return {
                "exit_code": 124,  # Standard timeout exit code
                "stdout": "",
                "stderr": f"Job timed out after {timeout} seconds"
            }
        except Exception as e:
            return {
                "exit_code": 1,
                "stdout": "",
                "stderr": f"Execution error: {str(e)}"
            }
        finally:
            # Cleanup job directory (optional, keep for debugging)
            # import shutil
            # shutil.rmtree(job_dir, ignore_errors=True)
            pass

    async def _run_command(
        self,
        command: str,
        cwd: Optional[Path] = None,
        timeout: int = 600,
        capture_output: bool = True
    ) -> Dict[str, Any]:
        """Run a shell command asynchronously."""

        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout
            )

            return {
                "exit_code": proc.returncode or 0,
                "stdout": stdout.decode("utf-8", errors="replace"),
                "stderr": stderr.decode("utf-8", errors="replace")
            }
        except asyncio.TimeoutError:
            proc.kill()
            raise

    def check_docker_available(self) -> bool:
        """Check if Docker is available and running."""
        try:
            result = subprocess.run(
                ["docker", "info"],
                capture_output=True,
                timeout=5
            )
            return result.returncode == 0
        except Exception:
            return False

    def get_system_info(self) -> Dict[str, Any]:
        """Get system information for registration."""
        import platform

        info = {
            "hostname": platform.node(),
            "os_type": platform.system(),
            "os_version": platform.release(),
            "cpu_cores": os.cpu_count(),
            "python_version": platform.python_version(),
            "docker_available": self.check_docker_available()
        }

        # Get memory info
        try:
            import psutil
            mem = psutil.virtual_memory()
            info["memory_gb"] = round(mem.total / (1024**3), 1)
            info["memory_available_gb"] = round(mem.available / (1024**3), 1)
        except ImportError:
            info["memory_gb"] = None
            info["memory_available_gb"] = None

        # Get Docker version
        if info["docker_available"]:
            try:
                result = subprocess.run(
                    ["docker", "version", "--format", "{{.Server.Version}}"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if result.returncode == 0:
                    info["docker_version"] = result.stdout.strip()
            except Exception:
                pass

        return info
