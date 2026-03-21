"""
Hot-Reload Manager

Manages service restart and hot-reload operations after code sync.
"""

import asyncio
import shlex
import subprocess
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional

import httpx

from persistence import PlatformUpdate


class ReloadAction(str, Enum):
    """Action to take for a service."""
    NONE = "none"           # No action needed
    RELOAD = "reload"       # Hot reload (signal)
    RESTART = "restart"     # Full restart
    REBUILD = "rebuild"     # Rebuild + restart


@dataclass
class ServiceConfig:
    """Configuration for a service."""
    name: str
    type: str  # "python", "nodejs", "static"
    working_dir: str
    restart_command: Optional[str] = None
    reload_signal: Optional[str] = None
    health_check_url: Optional[str] = None
    health_check_timeout: int = 30
    build_command: Optional[str] = None


@dataclass
class ReloadRule:
    """Rule for determining reload action based on file path."""
    path_pattern: str       # glob pattern
    action: ReloadAction
    services: List[str]     # affected services


class HotReloadManager:
    """
    Manages service restart and hot-reload operations.

    Determines which services need to be restarted based on changed files,
    executes the restart, and performs health checks.
    """

    ACTION_PRIORITY: Dict[ReloadAction, int] = {
        ReloadAction.NONE: 0,
        ReloadAction.RELOAD: 1,
        ReloadAction.RESTART: 2,
        ReloadAction.REBUILD: 3,
    }

    # Default service configurations
    DEFAULT_SERVICES: Dict[str, ServiceConfig] = {
        "api-gateway": ServiceConfig(
            name="api-gateway",
            type="python",
            working_dir="./apps/api-gateway",
            restart_command="pkill -HUP -f 'uvicorn.*api-gateway' || docker restart agenthub-api-gateway",
            reload_signal="HUP",
            health_check_url="http://localhost:8000/health",
            health_check_timeout=10,
        ),
        "observer-ui": ServiceConfig(
            name="observer-ui",
            type="nodejs",
            working_dir="./apps/observer-ui",
            restart_command="docker restart agenthub-observer-ui || pm2 reload observer-ui",
            build_command="npm run build",
            health_check_url="http://localhost:3000/api/health",
            health_check_timeout=15,
        ),
        "git-core": ServiceConfig(
            name="git-core",
            type="python",
            working_dir="./services/git-core",
            # Hooks are stateless, no restart needed
            restart_command=None,
        ),
    }

    # Default reload rules
    DEFAULT_RULES: List[ReloadRule] = [
        # Python backend changes
        ReloadRule(
            path_pattern="apps/api-gateway/**/*.py",
            action=ReloadAction.RESTART,
            services=["api-gateway"]
        ),
        ReloadRule(
            path_pattern="packages/protocol/**/*.py",
            action=ReloadAction.RESTART,
            services=["api-gateway"]
        ),
        # Frontend changes
        ReloadRule(
            path_pattern="apps/observer-ui/**/*",
            action=ReloadAction.REBUILD,
            services=["observer-ui"]
        ),
        # Git hooks are stateless
        ReloadRule(
            path_pattern="services/git-core/**/*.py",
            action=ReloadAction.NONE,
            services=[]
        ),
        # Infrastructure changes need manual restart
        ReloadRule(
            path_pattern="infra/**/*",
            action=ReloadAction.NONE,
            services=[]
        ),
    ]

    def __init__(
        self,
        services: Optional[Dict[str, ServiceConfig]] = None,
        rules: Optional[List[ReloadRule]] = None,
        health_check_retries: int = 3,
        rollback_on_failure: bool = True,
    ):
        self.services = services or self.DEFAULT_SERVICES
        self.rules = rules or self.DEFAULT_RULES
        self.health_check_retries = health_check_retries
        self.rollback_on_failure = rollback_on_failure

    def determine_actions(self, changed_files: List[str]) -> Dict[str, ReloadAction]:
        """
        Determine required actions for each service based on changed files.

        Args:
            changed_files: List of changed file paths

        Returns:
            Dict mapping service name to required action
        """
        import fnmatch

        actions: Dict[str, ReloadAction] = {}

        for rule in self.rules:
            for file_path in changed_files:
                if fnmatch.fnmatch(file_path, rule.path_pattern):
                    for service_name in rule.services:
                        # Only upgrade action, never downgrade
                        current = actions.get(service_name, ReloadAction.NONE)
                        if self.ACTION_PRIORITY[rule.action] > self.ACTION_PRIORITY[current]:
                            actions[service_name] = rule.action

        return actions

    @staticmethod
    def _run_safe_command(command: str, cwd: Optional[str] = None) -> subprocess.CompletedProcess:
        """Execute command without shell expansion."""
        tokens = shlex.split(command)
        return subprocess.run(tokens, shell=False, capture_output=True, text=True, cwd=cwd)

    def _run_fallback_commands(self, command: str, cwd: Optional[str] = None) -> subprocess.CompletedProcess:
        """Run command segments separated by shell fallback operator '||' safely."""
        segments = [seg.strip() for seg in command.split("||") if seg.strip()]
        if not segments:
            return subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="Empty command")

        last_result: Optional[subprocess.CompletedProcess] = None
        for seg in segments:
            result = self._run_safe_command(seg, cwd=cwd)
            if result.returncode == 0:
                return result
            last_result = result

        return last_result if last_result is not None else subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="Command failed")

    async def execute_reload(
        self,
        changed_files: List[str],
        update: PlatformUpdate,
    ) -> bool:
        """
        Execute hot-reload based on changed files.

        Args:
            changed_files: List of changed file paths
            update: PlatformUpdate record for logging

        Returns:
            True if all services reloaded successfully
        """
        actions = self.determine_actions(changed_files)
        results = {}

        log_lines = [f"[RELOAD] Starting hot-reload at {datetime.utcnow().isoformat()}"]
        log_lines.append(f"[RELOAD] Changed files: {len(changed_files)}")
        log_lines.append(f"[RELOAD] Actions: {actions}")

        for service_name, action in actions.items():
            if action == ReloadAction.NONE:
                log_lines.append(f"[RELOAD] {service_name}: No action needed")
                results[service_name] = True
                continue

            service_config = self.services.get(service_name)
            if not service_config:
                log_lines.append(f"[RELOAD] {service_name}: Unknown service config")
                results[service_name] = False
                continue

            success = await self._execute_service_action(
                service_name, action, service_config, log_lines
            )
            results[service_name] = success

            if not success and self.rollback_on_failure:
                log_lines.append(f"[RELOAD] Rollback triggered due to {service_name} failure")
                # Store log and signal failure
                update.deploy_log = "\n".join(log_lines)
                return False

        # Run health checks
        log_lines.append("[RELOAD] Running health checks...")
        for service_name in actions.keys():
            if actions[service_name] != ReloadAction.NONE:
                healthy = await self._health_check(service_name)
                if not healthy:
                    log_lines.append(f"[RELOAD] {service_name}: Health check FAILED")
                    results[service_name] = False
                else:
                    log_lines.append(f"[RELOAD] {service_name}: Health check passed")

        all_success = all(results.values())
        log_lines.append(f"[RELOAD] Completed: {'SUCCESS' if all_success else 'FAILED'}")

        update.deploy_log = "\n".join(log_lines)
        return all_success

    async def _execute_service_action(
        self,
        service_name: str,
        action: ReloadAction,
        config: ServiceConfig,
        log_lines: List[str],
    ) -> bool:
        """Execute action on a service."""
        try:
            if action == ReloadAction.RELOAD:
                # Send reload signal
                if config.reload_signal:
                    cmd = f"pkill -{config.reload_signal} -f {service_name}"
                    result = self._run_safe_command(cmd)
                    log_lines.append(
                        f"[RELOAD] {service_name}: Sent {config.reload_signal} signal"
                    )
                    return result.returncode == 0
                return True

            elif action == ReloadAction.RESTART:
                # Full restart
                if config.restart_command:
                    result = self._run_fallback_commands(
                        config.restart_command,
                        cwd=config.working_dir,
                    )
                    log_lines.append(
                        f"[RELOAD] {service_name}: Restart {'OK' if result.returncode == 0 else 'FAILED'}"
                    )
                    return result.returncode == 0
                return True

            elif action == ReloadAction.REBUILD:
                # Rebuild + restart
                if config.build_command:
                    result = self._run_safe_command(
                        config.build_command,
                        cwd=config.working_dir,
                    )
                    if result.returncode != 0:
                        log_lines.append(
                            f"[RELOAD] {service_name}: Build FAILED - {result.stderr[:200]}"
                        )
                        return False
                    log_lines.append(f"[RELOAD] {service_name}: Build OK")

                # Then restart
                if config.restart_command:
                    result = self._run_fallback_commands(
                        config.restart_command,
                        cwd=config.working_dir,
                    )
                    log_lines.append(
                        f"[RELOAD] {service_name}: Restart {'OK' if result.returncode == 0 else 'FAILED'}"
                    )
                    return result.returncode == 0
                return True

            return True

        except Exception as e:
            log_lines.append(f"[RELOAD] {service_name}: Exception - {str(e)}")
            return False

    async def _health_check(self, service_name: str) -> bool:
        """Check service health after reload."""
        config = self.services.get(service_name)
        if not config or not config.health_check_url:
            return True  # No health check configured

        async with httpx.AsyncClient() as client:
            for attempt in range(self.health_check_retries):
                try:
                    response = await client.get(
                        config.health_check_url,
                        timeout=config.health_check_timeout
                    )
                    if response.status_code == 200:
                        return True
                except Exception:
                    pass

                # Exponential backoff
                await asyncio.sleep(2 ** attempt)

        return False

    async def check_all_services_health(self) -> Dict[str, bool]:
        """
        Check health of all configured services.

        Returns:
            Dict mapping service name to health status
        """
        results = {}

        for service_name, config in self.services.items():
            if config.health_check_url:
                results[service_name] = await self._health_check(service_name)
            else:
                results[service_name] = True  # Assume healthy

        return results

    async def restart_service(self, service_name: str) -> bool:
        """
        Manually restart a specific service.

        Args:
            service_name: Name of service to restart

        Returns:
            True if successful
        """
        config = self.services.get(service_name)
        if not config:
            return False

        if config.restart_command:
            result = self._run_fallback_commands(
                config.restart_command,
                cwd=config.working_dir,
            )

            if result.returncode == 0:
                # Run health check
                return await self._health_check(service_name)
            return False

        return True  # No restart command, assume success
