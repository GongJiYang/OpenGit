import re
import shlex
from typing import List, Sequence


class ExecutionGuard:
    """
    Security and Cost-Control Guard for Sandbox Execution.
    Handles command validation, parameter filtering, and log sanitization.
    """

    # Keep this allowlist aligned with API validation strategy.
    ALLOWED_TEST_COMMANDS = {"pytest", "python", "python3", "tox", "nose"}
    # Backward-compatible alias for existing callers.
    ALLOWED_COMMANDS = ALLOWED_TEST_COMMANDS

    # Strictly prohibited patterns to prevent shell escapes and high-risk operations
    PROHIBITED_PATTERNS = {";", "&", "|", ">", "<", "`", "$", "sudo", "chmod", "chown"}

    _ALLOWED_PYTHON_MODULES = {"pytest", "unittest", "nose", "tox"}

    @staticmethod
    def verify_command(command_str: str) -> List[str]:
        """
        Parses and validates a command string.
        Returns a list of tokens if valid, otherwise raises a ValueError.
        """
        if not command_str or not command_str.strip():
            raise ValueError("Command cannot be empty")

        # 1. Check for prohibited patterns before parsing
        for pattern in ExecutionGuard.PROHIBITED_PATTERNS:
            if pattern in command_str:
                raise ValueError(f"Command contains prohibited character/pattern: {pattern}")

        # 2. Parse using shlex
        try:
            tokens = shlex.split(command_str)
        except Exception as e:
            raise ValueError(f"Failed to parse command: {str(e)}")

        if not tokens:
            raise ValueError("Parsed command is empty")

        # 3. Whitelist check for the base command
        base_cmd = tokens[0]
        if base_cmd not in ExecutionGuard.ALLOWED_TEST_COMMANDS:
            raise ValueError(
                f"Command '{base_cmd}' is not in the whitelist: {sorted(ExecutionGuard.ALLOWED_TEST_COMMANDS)}"
            )

        # 4. Additional hardening for python invocations
        if base_cmd in {"python", "python3"}:
            ExecutionGuard._validate_python_tokens(tokens)

        return tokens

    @staticmethod
    def _validate_python_tokens(tokens: Sequence[str]) -> None:
        # Keep backward compatibility for existing records using base command only ("python").
        if len(tokens) == 1:
            return

        second = tokens[1]

        # Block obvious arbitrary-code execution path
        if second == "-c":
            raise ValueError("Inline python execution is not allowed")

        # Allow only test-oriented modules
        if second == "-m":
            if len(tokens) < 3:
                raise ValueError("python -m requires a module name")
            module_name = tokens[2]
            if module_name not in ExecutionGuard._ALLOWED_PYTHON_MODULES:
                raise ValueError(
                    f"python -m only allows: {sorted(ExecutionGuard._ALLOWED_PYTHON_MODULES)}"
                )
            return

        # Other flags are denied by default
        if second.startswith("-"):
            raise ValueError("Unsupported python flags")

        # Script execution is allowed only for local relative python files
        if second.startswith("/") or ".." in second:
            raise ValueError("Absolute paths or parent directory escapes are not allowed for python scripts")

        if not second.endswith(".py"):
            raise ValueError("python/python3 can only run .py scripts or -m with approved test modules")

    @staticmethod
    def sanitize_output(output: str, max_length: int = 200) -> str:
        """
        Masks potential secrets and truncates log output.
        """
        if not output:
            return ""

        # Mask common API Key patterns (Basic regex)
        # Patterns like: key-abc1234, sk-..., etc.
        patterns = [
            r"(?i)(api[-_]?key|secret|token|password)[\s:=]+([a-z0-9\-_]{8,})",
            r"(?i)(sk-[a-zA-Z0-9]{20,})"
        ]

        sanitized = output
        for p in patterns:
            sanitized = re.sub(p, r"\1: [MASKED]", sanitized)

        # Truncate to last N characters
        if len(sanitized) > max_length:
            return "... [TRUNCATED] ...\n" + sanitized[-max_length:]

        return sanitized

    @staticmethod
    def estimate_cost(is_new_session: bool, command_count: int = 1) -> float:
        """
        Rough cost estimation in USD.
        Command: ~$0.001 per command trace.
        """
        return command_count * 0.001
