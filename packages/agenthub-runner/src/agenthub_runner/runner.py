"""
AgentHub Runner API Client

Handles all HTTP communication with the AgentHub platform.
"""

from typing import Any, Dict, List, Optional

import httpx


class RunnerClient:
    """HTTP client for AgentHub Runner API."""

    def __init__(
        self,
        api_base: str = "https://api.agenthub.dev",
        auth_token: Optional[str] = None,
        timeout: float = 30.0
    ):
        self.api_base = api_base.rstrip("/")
        self.auth_token = auth_token
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            headers = {}
            if self.auth_token:
                headers["X-Runner-Token"] = self.auth_token
            self._client = httpx.AsyncClient(
                base_url=self.api_base,
                headers=headers,
                timeout=self.timeout
            )
        return self._client

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None

    async def register(
        self,
        token: str,
        name: str,
        cpu_cores: int,
        memory_gb: float,
        os_type: str,
        os_version: str,
        docker_version: Optional[str] = None,
        labels: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Register this runner with a one-time token.

        Returns runner info and permanent auth token.
        """
        client = await self._get_client()

        response = await client.post(
            "/api/v1/runners/register",
            json={
                "token": token,
                "name": name,
                "cpu_cores": cpu_cores,
                "memory_gb": memory_gb,
                "os_type": os_type,
                "os_version": os_version,
                "docker_version": docker_version,
                "labels": labels or []
            }
        )

        if response.status_code == 200:
            return response.json()

        return {
            "success": False,
            "detail": response.json().get("detail", f"HTTP {response.status_code}")
        }

    async def heartbeat(self) -> Dict[str, Any]:
        """
        Send heartbeat to indicate runner is alive.

        Should be called every 30 seconds.
        """
        client = await self._get_client()

        response = await client.post(
            "/api/v1/runners/heartbeat",
            headers={"X-Runner-Token": self.auth_token}
        )

        if response.status_code == 200:
            return response.json()

        raise Exception(f"Heartbeat failed: {response.json().get('detail', 'Unknown error')}")

    async def poll_jobs(self, max_jobs: int = 1) -> List[Dict[str, Any]]:
        """
        Poll for available jobs.

        This is the core of the reverse long-polling architecture.
        Should be called every 5 seconds.
        """
        client = await self._get_client()

        response = await client.get(
            "/api/v1/runners/poll-jobs",
            params={"max_jobs": max_jobs},
            headers={"X-Runner-Token": self.auth_token}
        )

        if response.status_code == 200:
            return response.json()

        # No jobs or error - return empty list
        return []

    async def submit_result(
        self,
        job_id: str,
        exit_code: int,
        stdout_log: str,
        stderr_log: str = "",
        test_results: Optional[Dict] = None,
        passed: bool = True
    ) -> Dict[str, Any]:
        """
        Submit job execution results.

        Zero-Trust: stdout_log is MANDATORY and must be meaningful.
        """
        client = await self._get_client()

        response = await client.post(
            "/api/v1/runners/submit-result",
            headers={"X-Runner-Token": self.auth_token},
            json={
                "job_id": job_id,
                "exit_code": exit_code,
                "stdout_log": stdout_log,
                "stderr_log": stderr_log,
                "test_results": test_results,
                "passed": passed
            }
        )

        if response.status_code == 200:
            return response.json()

        raise Exception(f"Submit failed: {response.json().get('detail', 'Unknown error')}")


# Import psutil for system info (with fallback)
try:
    import psutil
except ImportError:
    psutil = None  # type: ignore
