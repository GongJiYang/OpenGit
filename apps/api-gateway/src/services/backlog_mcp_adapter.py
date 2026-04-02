from __future__ import annotations

import asyncio
import json
import os
import shlex
from typing import Any, Dict, Optional, Sequence


class BacklogMcpAdapterError(RuntimeError):
    """Raised when stdio MCP invocation fails."""


class BacklogMcpAdapter:
    """Minimal stdio MCP adapter for backlog governance calls."""

    def __init__(
        self,
        command: Optional[Sequence[str]] = None,
        timeout_seconds: Optional[float] = None,
    ):
        self.command = list(command) if command is not None else self._resolve_command_from_env()
        self.timeout_seconds = timeout_seconds if timeout_seconds is not None else self._resolve_timeout_from_env()

    @staticmethod
    def _resolve_command_from_env() -> list[str]:
        raw = (os.getenv("BACKLOG_MCP_COMMAND") or "").strip()
        if not raw:
            return []
        try:
            return shlex.split(raw)
        except ValueError as exc:
            raise BacklogMcpAdapterError("Invalid BACKLOG_MCP_COMMAND") from exc

    @staticmethod
    def _resolve_timeout_from_env() -> float:
        raw = (os.getenv("BACKLOG_MCP_TIMEOUT_SECONDS") or "30").strip()
        try:
            timeout = float(raw)
        except ValueError:
            timeout = 30.0
        return max(0.1, timeout)

    def is_configured(self) -> bool:
        return bool(self.command)

    async def start(self, repo_name: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        params: Dict[str, Any] = {"repo_name": repo_name}
        if payload:
            params.update(payload)
        return await self.call("backlog.start", params)

    async def call(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if not self.command:
            raise BacklogMcpAdapterError("BACKLOG_MCP_COMMAND is not configured")

        request_payload: Dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": "1",
            "method": method,
        }
        if params is not None:
            request_payload["params"] = params

        try:
            process = await asyncio.create_subprocess_exec(
                *self.command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise BacklogMcpAdapterError("Backlog MCP command executable not found") from exc

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate((json.dumps(request_payload, ensure_ascii=False) + "\n").encode("utf-8")),
                timeout=self.timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            process.kill()
            await process.wait()
            raise BacklogMcpAdapterError(
                f"Backlog MCP call timed out after {self.timeout_seconds:.1f}s"
            ) from exc

        if process.returncode != 0:
            error_text = stderr.decode("utf-8", errors="ignore").strip()
            raise BacklogMcpAdapterError(
                f"Backlog MCP process exited with code {process.returncode}: {error_text or 'unknown error'}"
            )

        response = self._parse_response(stdout.decode("utf-8", errors="ignore"))

        if isinstance(response, dict) and response.get("error"):
            error_obj = response.get("error")
            if isinstance(error_obj, dict):
                message = error_obj.get("message") or str(error_obj)
            else:
                message = str(error_obj)
            raise BacklogMcpAdapterError(f"Backlog MCP error: {message}")

        if isinstance(response, dict) and "result" in response:
            result = response["result"]
            if isinstance(result, dict):
                return result
            return {"result": result}

        if isinstance(response, dict):
            return response

        return {"result": response}

    @staticmethod
    def _parse_response(raw_output: str) -> Dict[str, Any]:
        lines = [line.strip() for line in raw_output.splitlines() if line.strip()]
        if not lines:
            raise BacklogMcpAdapterError("Backlog MCP returned empty response")

        for line in reversed(lines):
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed

        raise BacklogMcpAdapterError("Backlog MCP returned non-JSON response")
