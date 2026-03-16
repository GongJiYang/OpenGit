"""
Structured Output Validator

Validates that Agent outputs conform to expected structured formats.
This prevents Agents from asking open-ended questions and forces them
to provide concrete, actionable options.

Core Rules:
1. Output must be valid JSON array
2. Array length must be 3-5 options
3. Each option must have required fields (option, reason)
4. No forbidden phrases (questions, deflections)
"""

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List, Dict


class ValidationErrorType(str, Enum):
    """Types of validation errors."""
    INVALID_JSON = "invalid_json"
    WRONG_TYPE = "wrong_type"
    WRONG_LENGTH = "wrong_length"
    MISSING_FIELD = "missing_field"
    FORBIDDEN_PHRASE = "forbidden_phrase"
    EMPTY_CONTENT = "empty_content"


@dataclass
class ValidationResult:
    """Result of output validation."""
    is_valid: bool
    error_type: Optional[ValidationErrorType] = None
    error_message: Optional[str] = None
    parsed_options: Optional[List[Dict[str, str]]] = None
    penalty_points: int = 0


@dataclass
class ValidationConfig:
    """Configuration for output validation."""
    min_options: int = 3
    max_options: int = 5
    required_fields: List[str] = field(default_factory=lambda: ["option", "reason"])
    forbidden_patterns: List[str] = field(default_factory=lambda: [
        r"[？?](\s|$)",  # Questions
        r"取决于",
        r"请问",
        r"你觉得",
        r"你认为",
        r"请告诉我",
        r"能否",
        r"可以吗",
        r"怎么样",
        r"如何选择",
        r"depends on",
        r"what do you",
        r"could you",
        r"would you",
        r"can you tell",
        r"how about",
        r"what if",
        r"不确定",
        r"无法确定",
        r"需要更多信息",
        r"need more info",
        r"not sure",
        r"unclear",
    ])
    penalty_per_violation: int = 10
    max_violations_before_suspend: int = 3


class StructuredOutputValidator:
    """
    Validates Agent outputs for structured format compliance.

    Usage:
        validator = StructuredOutputValidator()
        result = validator.validate(agent_output)

        if not result.is_valid:
            # Reject submission, apply penalty
            pass
        else:
            # Process the parsed options
            options = result.parsed_options
    """

    def __init__(self, config: Optional[ValidationConfig] = None):
        self.config = config or ValidationConfig()
        self._compile_patterns()

    def _compile_patterns(self):
        """Pre-compile regex patterns for performance."""
        self._forbidden_regex = [
            re.compile(pattern, re.IGNORECASE)
            for pattern in self.config.forbidden_patterns
        ]

    def validate(self, output: str) -> ValidationResult:
        """
        Validate Agent output against structured format rules.

        Args:
            output: Raw output string from Agent

        Returns:
            ValidationResult with validity status and parsed data
        """
        if not output or not output.strip():
            return ValidationResult(
                is_valid=False,
                error_type=ValidationErrorType.EMPTY_CONTENT,
                error_message="Output is empty",
                penalty_points=self.config.penalty_per_violation
            )

        # Step 1: Parse JSON
        try:
            # Try to extract JSON from markdown code blocks if present
            json_str = self._extract_json(output)
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            return ValidationResult(
                is_valid=False,
                error_type=ValidationErrorType.INVALID_JSON,
                error_message=f"Invalid JSON format: {str(e)}",
                penalty_points=self.config.penalty_per_violation
            )

        # Step 2: Check type (must be array)
        if not isinstance(data, list):
            return ValidationResult(
                is_valid=False,
                error_type=ValidationErrorType.WRONG_TYPE,
                error_message=f"Expected array, got {type(data).__name__}",
                penalty_points=self.config.penalty_per_violation
            )

        # Step 3: Check length
        if not (self.config.min_options <= len(data) <= self.config.max_options):
            return ValidationResult(
                is_valid=False,
                error_type=ValidationErrorType.WRONG_LENGTH,
                error_message=f"Expected {self.config.min_options}-{self.config.max_options} options, got {len(data)}",
                penalty_points=self.config.penalty_per_violation
            )

        # Step 4: Check required fields in each option
        for i, option in enumerate(data):
            if not isinstance(option, dict):
                return ValidationResult(
                    is_valid=False,
                    error_type=ValidationErrorType.WRONG_TYPE,
                    error_message=f"Option {i+1} is not an object",
                    penalty_points=self.config.penalty_per_violation
                )

            missing = [f for f in self.config.required_fields if f not in option]
            if missing:
                return ValidationResult(
                    is_valid=False,
                    error_type=ValidationErrorType.MISSING_FIELD,
                    error_message=f"Option {i+1} missing fields: {', '.join(missing)}",
                    penalty_points=self.config.penalty_per_violation
                )

        # Step 5: Check for forbidden phrases
        full_text = json.dumps(data, ensure_ascii=False)
        for pattern in self._forbidden_regex:
            if pattern.search(full_text):
                return ValidationResult(
                    is_valid=False,
                    error_type=ValidationErrorType.FORBIDDEN_PHRASE,
                    error_message=f"Output contains forbidden pattern: {pattern.pattern}",
                    penalty_points=self.config.penalty_per_violation
                )

        # All checks passed
        return ValidationResult(
            is_valid=True,
            parsed_options=data
        )

    def _extract_json(self, output: str) -> str:
        """Extract JSON from output, handling markdown code blocks."""
        output = output.strip()

        # Check for markdown code block
        if "```json" in output:
            match = re.search(r'```json\s*([\s\S]*?)\s*```', output)
            if match:
                return match.group(1)
        elif "```" in output:
            match = re.search(r'```\s*([\s\S]*?)\s*```', output)
            if match:
                return match.group(1)

        # No code block, assume raw JSON
        return output

    def validate_with_retry_prompt(self, output: str) -> tuple[ValidationResult, Optional[str]]:
        """
        Validate output and generate retry prompt if invalid.

        Returns:
            Tuple of (ValidationResult, retry_prompt or None)
        """
        result = self.validate(output)

        if result.is_valid:
            return result, None

        retry_prompt = self._generate_retry_prompt(result)
        return result, retry_prompt

    def _generate_retry_prompt(self, result: ValidationResult) -> str:
        """Generate a retry prompt based on the validation error."""
        base_prompt = """你的输出格式不符合规范，已被自动驳回。

请重新生成，严格遵守以下规则：
1. 输出必须是严格的 JSON 数组
2. 数组长度必须在 3-5 之间
3. 每个对象必须包含 "option" 和 "reason" 字段
4. 禁止包含任何问句、反问或推诿性文字
5. 禁止说"取决于"、"请告诉我更多"等

正确格式示例：
```json
[
  {"option": "方案一名称", "reason": "选择理由一句话"},
  {"option": "方案二名称", "reason": "选择理由一句话"},
  {"option": "方案三名称", "reason": "选择理由一句话"}
]
```

现在请重新输出。"""

        error_hint = f"\n\n[错误详情] {result.error_message}"
        return base_prompt + error_hint


# Singleton instance for convenience
_default_validator = None

def get_validator() -> StructuredOutputValidator:
    """Get the default validator instance."""
    global _default_validator
    if _default_validator is None:
        _default_validator = StructuredOutputValidator()
    return _default_validator
