import requests
from typing import List, Optional
from pydantic import BaseModel, Field
from ..base import Skill

API_BASE = "http://127.0.0.1:8000"

class CreateWorkItemArgs(BaseModel):
    title: str = Field(..., description="Title of the task")
    description: str = Field(..., description="Detailed description of the task")
    reward: int = Field(..., description="Token reward for completion")
    repo_name: str = Field(..., description="Target repository name (e.g. 'snake.git')")
    required_role: str = Field(..., description="Role required (contributor/executor)")
    context_files: List[str] = Field(default=[], description="List of interface files to read")
    target_files: List[str] = Field(default=[], description="List of files to implement")
    acceptance_criteria: str = Field(default="", description="Criteria for verification")

class CreateWorkItemSkill(Skill):
    name = "create_work_item"
    description = "Creates a new task/bounty for another agent to pick up."
    input_schema = CreateWorkItemArgs

    def execute(self, title: str, description: str, reward: int, repo_name: str, 
                required_role: str, context_files: List[str], target_files: List[str], 
                acceptance_criteria: str) -> str:
        
        payload = {
            "title": title,
            "description": description,
            "reward": reward,
            "repo_name": repo_name,
            "required_role": required_role,
            "context_files": context_files,
            "target_files": target_files,
            "acceptance_criteria": acceptance_criteria
        }
        
        try:
            res = requests.post(f"{API_BASE}/bounties", json=payload)
            if res.status_code == 200:
                return f"Successfully created task: {title}"
            else:
                return f"Failed to create task: {res.text}"
        except Exception as e:
            return f"Network error creating task: {str(e)}"

class DefineInterfaceArgs(BaseModel):
    path: str = Field(..., description="Absolute path to the interface file")
    content: str = Field(..., description="Code content (signatures/types only)")

class DefineInterfaceSkill(Skill):
    name = "define_interface"
    description = "Writes an interface definition file (e.g. .pyi or abstract base class). NOT for implementation."
    input_schema = DefineInterfaceArgs

    def execute(self, path: str, content: str) -> str:
        # Wrapper around file write, but semantically distinct for the LLM
        try:
            import os
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w', encoding='utf-8') as f:
                f.write(content)
            return f"Successfully defined interface at {path}"
        except Exception as e:
            return f"Error defining interface: {str(e)}"
