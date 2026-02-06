import requests
from pydantic import BaseModel, Field
from ..base import Skill

API_BASE = "http://localhost:8000"

class ClaimTaskArgs(BaseModel):
    task_id: str = Field(..., description="ID of the task/bounty to claim")
    agent_id: str = Field(..., description="Your Agent ID")

class ClaimTaskSkill(Skill):
    name = "claim_task"
    description = "Claims a specific task. Returns the task details (WorkItem) including context and requirements."
    input_schema = ClaimTaskArgs

    def execute(self, task_id: str, agent_id: str) -> dict:
        try:
            res = requests.post(f"{API_BASE}/bounties/{task_id}/claim", params={"agent_id": agent_id})
            if res.status_code == 200:
                data = res.json()
                # Return a simplified view for the LLM
                return {
                    "status": "success",
                    "task_title": data['title'],
                    "description": data['description'],
                    "context_files": data.get('context_files', []),
                    "target_files": data.get('target_files', []),
                    "acceptance_criteria": data.get('acceptance_criteria', "None")
                }
            else:
                return {"status": "error", "message": res.text}
        except Exception as e:
            return {"status": "error", "message": str(e)}

class SubmitTaskArgs(BaseModel):
    task_id: str = Field(..., description="ID of the task being submitted")
    summary: str = Field(..., description="Summary of work done")

class SubmitTaskSkill(Skill):
    name = "submit_task"
    description = "Submits a completed task for review."
    input_schema = SubmitTaskArgs

    def execute(self, task_id: str, summary: str) -> str:
        # MVP: Just log it or mock an API call
        # In real implementation: POST /bounties/{id}/submit
        return f"Task {task_id} submitted successfully with summary: {summary}"
