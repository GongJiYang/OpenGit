from typing import Dict, Any, List, Optional
from pydantic import ValidationError
from .schemas import TraceCommit

class TraceValidator:
    """
    Enforces the 'Trace-Commit' protocol.
    Ensures that every commit has a valid reasoning trace and intent.
    """

    @staticmethod
    def validate_commit(commit_data: Dict[str, Any]) -> TraceCommit:
        """
        Validates raw dictionary data against the TraceCommit schema.
        Raises ValueError if validation fails or logic rules are violated.
        """
        try:
            # 1. Schema Validation
            commit = TraceCommit(**commit_data)
        except ValidationError as e:
            raise ValueError(f"Schema Validation Failed: {e}")

        # 2. Logic Validation
        
        # Rule 1: Reasoning Trace must not be empty
        if not commit.reasoning_trace or len(commit.reasoning_trace) == 0:
             raise ValueError("Protocol Violation: 'reasoning_trace' cannot be empty. Agents must explain *why*.")
        
        # Rule 2: Intent Vector must be present (if we enforce vectors at this stage)
        # For now, we check if description is present in intent
        if not commit.intent.description:
             raise ValueError("Protocol Violation: 'intent.description' is missing.")

        return commit

    @staticmethod
    def check_quality(commit: TraceCommit) -> List[str]:
        """
        Optional: Returns warnings about the quality of the trace.
        """
        warnings = []
        if len(commit.reasoning_trace) < 3:
            warnings.append("Weak Reasoning: Trace has fewer than 3 steps.")
        
        if len(commit.diff_summary) < 10:
             warnings.append("Weak Summary: Diff summary is too short.")
             
        return warnings
