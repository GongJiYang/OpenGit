import re
import shlex
from typing import List, Optional


class ExecutionGuard:
    """
    Security and Cost-Control Guard for Sandbox Execution.
    Handles command validation, parameter filtering, and log sanitization.
    """

    # Keep this allowlist aligned with API validation strategy.
    ALLOWED_TEST_COMMANDS = {"pytest"}
    # Backward-compatible alias for existing callers.
    ALLOWED_COMMANDS = ALLOWED_TEST_COMMANDS

    # Strictly prohibited patterns to prevent shell escapes and high-risk operations
    PROHIBITED_PATTERNS = {";", "&", "|", ">", "<", "`", "$", "sudo", "chmod", "chown"}

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

        return tokens

    @staticmethod
    def sanitize_output(output: str, max_length: int = 200) -> str:
        """
        Masks potential secrets and truncates log output.
        """
        if not output:
            return ""

        sanitized = output

        # Authorization headers with bearer/basic-ish tokens
        sanitized = re.sub(
            r"(?im)(\bauthorization\b\s*(?:=|:)\s*(?:bearer\s+)?)([^\s\",;]{6,})",
            r"\1[MASKED]",
            sanitized,
        )

        # Structured secret fields: key=value / key:value / key value
        sanitized = re.sub(
            r"(?im)(\b(?:api[-_]?key|secret|token|password|access[-_]?token|refresh[-_]?token)\b\s*(?:=|:|\s)\s*)([^\s\",;]{6,})",
            r"\1[MASKED]",
            sanitized,
        )

        # Bearer tokens in free text
        sanitized = re.sub(r"(?i)\b(bearer\s+)([A-Za-z0-9._\-+/=]{8,})", r"\1[MASKED]", sanitized)

        # JWT-like tokens
        sanitized = re.sub(
            r"\b([A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,})\b",
            "[MASKED_JWT]",
            sanitized,
        )

        # Vendor/common token prefixes
        sanitized = re.sub(r"\b(gh[pousr]_[A-Za-z0-9]{20,})\b", "[MASKED]", sanitized)
        sanitized = re.sub(r"\b(sk-[A-Za-z0-9]{20,})\b", "[MASKED]", sanitized)

        # Private key blocks
        sanitized = re.sub(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----",
            "[MASKED_PRIVATE_KEY]",
            sanitized,
        )

        # URL query tokens/secrets
        sanitized = re.sub(
            r"(?i)([?&](?:token|access_token|refresh_token|api_key|apikey|key|signature)=)([^&\s]+)",
            r"\1[MASKED]",
            sanitized,
        )

        # Truncate to head+tail windows for better debuggability
        if max_length <= 0:
            return ""

        if len(sanitized) <= max_length:
            return sanitized

        if max_length < 20:
            return sanitized[:max_length]

        head_len = max(1, int(max_length * 0.6))
        tail_len = max(1, max_length - head_len)
        omitted = len(sanitized) - head_len - tail_len
        return (
            sanitized[:head_len]
            + f"\n... [TRUNCATED {omitted} chars] ...\n"
            + sanitized[-tail_len:]
        )

    @staticmethod
    def estimate_cost(
        is_new_session: bool,
        command_count: int = 1,
        timeout_seconds: int = 300,
        command_str: Optional[str] = None,
        sandbox_provider: str = "disabled",
        cpu_cores: Optional[int] = None,
    ) -> float:
        """
        Lightweight multi-factor cost estimate in USD.

        Factors:
        - Session setup overhead
        - Command count and command token length
        - Timeout budget (proxy for runtime upper bound)
        - Sandbox provider execution profile
        - Runner CPU capability (higher-capability runner gets a small multiplier)
        """
        safe_command_count = max(1, int(command_count or 1))
        safe_timeout = max(1, int(timeout_seconds or 1))

        # Baseline + per-command.
        base = 0.0003
        per_command = 0.0004 * safe_command_count

        # Session overhead.
        session_overhead = 0.0003 if is_new_session else 0.0

        # Command complexity by token length.
        token_count = 0
        if command_str and command_str.strip():
            try:
                token_count = len(shlex.split(command_str))
            except Exception:
                token_count = max(1, len(command_str.strip().split()))
        token_factor = 0.00005 * max(1, token_count)

        # Timeout cost scales gently with upper bound.
        timeout_factor = 0.0002 * (safe_timeout / 300.0)

        provider = (sandbox_provider or "disabled").strip().lower()
        provider_multiplier = {
            "disabled": 1.0,
            "subprocess": 1.3,
            "runner": 1.8,
        }.get(provider, 1.0)

        cpu_factor = 1.0
        if cpu_cores and cpu_cores > 0:
            cpu_factor += min(0.5, max(0.0, (cpu_cores - 2) * 0.03))

        estimate = (base + per_command + session_overhead + token_factor + timeout_factor)
        estimate *= provider_multiplier
        estimate *= cpu_factor

        # Keep a deterministic floor to avoid zero/near-zero estimates.
        return max(0.0005, round(estimate, 6))
