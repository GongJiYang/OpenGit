import shlex
import re
from typing import List, Optional

class ExecutionGuard:
    """
    Security and Cost-Control Guard for Sandbox Execution.
    Handles command validation, parameter filtering, and log sanitization.
    """
    
    ALLOWED_COMMANDS = {"pytest", "npm", "python", "ls", "cat", "mkdir", "touch", "rm"}
    
    # Strictly prohibited patterns to prevent shell escapes and high-risk operations
    PROHIBITED_PATTERNS = {";", "&", "|", ">", "<", "`", "$", "sudo", "chmod", "chown"}

    @staticmethod
    def verify_command(command_str: str) -> List[str]:
        """
        Parses and validates a command string. 
        Returns a list of tokens if valid, otherwise raises a ValueError.
        """
        if not command_str:
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
        if base_cmd not in ExecutionGuard.ALLOWED_COMMANDS:
            raise ValueError(f"Command '{base_cmd}' is not in the whitelist: {ExecutionGuard.ALLOWED_COMMANDS}")

        # 4. Specific validation for common commands
        if base_cmd == "rm" and len(tokens) > 1:
             # Prevent rm -rf / or other dangerous deletions
             for t in tokens[1:]:
                 if t.startswith("/") or ".." in t:
                     raise ValueError("Absolute paths or parent directory escapes are not allowed in 'rm'")

        return tokens

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
        E2B Session: ~$0.05 per start (approximation of overhead/lifecycle).
        Command: ~$0.001 per command trace.
        """
        cost = 0.0
        if is_new_session:
            cost += 0.05
        cost += command_count * 0.001
        return cost
