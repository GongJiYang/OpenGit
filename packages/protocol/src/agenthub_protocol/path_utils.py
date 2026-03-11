from pathlib import Path
from typing import Union

def ensure_safe_path(base: Union[str, Path], path: Union[str, Path], error_message: str = "Path traversal detected") -> Path:
    """
    Ensure that a path is relative to a base directory and doesn't escape it.
    
    Args:
        base: The base directory (must be safe).
        path: The user-provided path (potentially unsafe).
        error_message: The message to include in the ValueError.
        
    Returns:
        Path: The resolved absolute safe path.
    
    Raises:
        ValueError: If the path escapes the base directory.
    """
    base_path = Path(base).resolve()
    # Handle both: path as relative or absolute
    target = (base_path / path).resolve()
    
    if not target.is_relative_to(base_path):
        raise ValueError(f"{error_message}: {path}")
    
    return target
