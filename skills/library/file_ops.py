import os
import sys
from pathlib import Path
from pydantic import BaseModel, Field

# --- Hack for Monorepo Paths ---
base_dir = os.path.dirname(os.path.abspath(__file__))
protocol_path = os.path.abspath(os.path.join(base_dir, "../../packages/protocol/src"))
if protocol_path not in sys.path:
    sys.path.append(protocol_path)

from agenthub_protocol.path_utils import ensure_safe_path
from ..base import Skill

class ReadFileArgs(BaseModel):
    path: str = Field(..., description="Path to the file to read (relative to workspace if root_dir is set)")

class ReadFileSkill(Skill):
    name = "read_file"
    description = "Reads the content of a file from the local filesystem."
    input_schema = ReadFileArgs

    def execute(self, path: str) -> str:
        try:
            if self.root_dir:
                try:
                    full_path = ensure_safe_path(self.root_dir, path, f"Access denied. Path {path} is outside the workspace")
                except ValueError as e:
                    return f"Error: {str(e)}"
            else:
                target_path = Path(path)
                full_path = target_path.resolve()
                if not target_path.is_absolute():
                    return "Error: Path must be absolute when no root_dir is configured."
            
            if not full_path.exists():
                return f"Error: File not found at {full_path}"

            with open(full_path, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception as e:
            return f"Error reading file: {str(e)}"

class WriteFileArgs(BaseModel):
    path: str = Field(..., description="Path to the file to write (relative to workspace if root_dir is set)")
    content: str = Field(..., description="Content to write to the file")

class WriteFileSkill(Skill):
    name = "write_file"
    description = "Writes content to a file. Overwrites if exists."
    input_schema = WriteFileArgs

    def execute(self, path: str, content: str) -> str:
        try:
            if self.root_dir:
                try:
                    full_path = ensure_safe_path(self.root_dir, path, f"Access denied. Path {path} is outside the workspace")
                except ValueError as e:
                    return f"Error: {str(e)}"
            else:
                target_path = Path(path)
                full_path = target_path.resolve()
                if not target_path.is_absolute():
                    return "Error: Path must be absolute when no root_dir is configured."

            # Ensure dir exists
            os.makedirs(full_path.parent, exist_ok=True)
            with open(full_path, 'w', encoding='utf-8') as f:
                f.write(content)
            return f"Successfully wrote to {full_path}"
        except Exception as e:
            return f"Error writing file: {str(e)}"
