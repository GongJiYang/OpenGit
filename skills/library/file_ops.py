import os
from pydantic import BaseModel, Field
from ..base import Skill

class ReadFileArgs(BaseModel):
    path: str = Field(..., description="Absolute path to the file to read")

class ReadFileSkill(Skill):
    name = "read_file"
    description = "Reads the content of a file from the local filesystem."
    input_schema = ReadFileArgs

    def execute(self, path: str) -> str:
        if not os.path.isabs(path):
            return "Error: Path must be absolute."
        
        if not os.path.exists(path):
            return f"Error: File not found at {path}"

        try:
            with open(path, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception as e:
            return f"Error reading file: {str(e)}"

class WriteFileArgs(BaseModel):
    path: str = Field(..., description="Absolute path to the file to write")
    content: str = Field(..., description="Content to write to the file")

class WriteFileSkill(Skill):
    name = "write_file"
    description = "Writes content to a file. Overwrites if exists."
    input_schema = WriteFileArgs

    def execute(self, path: str, content: str) -> str:
        try:
            # Ensure dir exists
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w', encoding='utf-8') as f:
                f.write(content)
            return f"Successfully wrote to {path}"
        except Exception as e:
            return f"Error writing file: {str(e)}"
