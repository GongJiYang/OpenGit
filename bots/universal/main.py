import sys
import os
import time
import requests
import importlib

# Add root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from bots.base_agent import BaseAgent
from bots.architect.main import Architect
from bots.contributor.main import Contributor
from bots.executor.main import Executor

API_URL = "http://127.0.0.1:8000"

class UniversalAgent(BaseAgent):
    def __init__(self, agent_id: str):
        super().__init__(agent_id, "universal")
        self.role_map = {
            "architect": Architect,
            "contributor": Contributor,
            "executor": Executor
        }

    def start(self):
        self.log("Online. Waiting for jobs...", "🟢")
        while True:
            try:
                # 1. Poll for Bounties
                res = requests.get(f"{API_URL}/bounties")
                if res.status_code != 200:
                    time.sleep(5)
                    continue
                
                bounties = res.json()
                open_bounties = [b for b in bounties if b['status'] == 'open']

                if not open_bounties:
                    print(".", end="", flush=True)
                    time.sleep(3)
                    continue

                # 2. Claim first available
                job = open_bounties[0]
                self.log(f"\nFound Job: {job['title']} (${job['reward']})", "👀")
                
                claim_res = requests.post(f"{API_URL}/bounties/{job['id']}/claim", params={"agent_id": self.agent_id})
                
                if claim_res.status_code == 200:
                    self.log(f"Claimed Job {job['id']}! Switching Role -> {job['required_role']}", "🔄")
                    self.execute_job(job)
                else:
                    self.log("Failed to claim job.", "❌")

            except Exception as e:
                self.log(f"Error: {e}", "⚠️")
                time.sleep(5)

    def execute_job(self, job):
        role_cls = self.role_map.get(job['required_role'])
        if not role_cls:
            self.log(f"Unknown Role: {job['required_role']}", "❓")
            return

        # Instantiate specific agent
        # Note: In a real system, we would inject specific params.
        # Here we just run their default 'run' method which might need args.
        # We customized the bots to take (repo_name) as arg.
        
        agent_instance = role_cls()
        self.log(f"Running as {agent_instance.role.upper()} on {job['repo_name']}...", "🚀")
        
        # We need to adapt the .run() method of agents to be callable here.
        # Architect.run(project_name)
        # Contributor.run(project_name)
        # Executor.run(project_name)
        
        try:
             # Run the logic
             agent_instance.run(job['repo_name'])
             self.log(f"Job {job['id']} Completed.", "✅")
             # Ideally we call POST /bounties/{id}/complete here
        except Exception as e:
             self.log(f"Job Failed: {e}", "💥")

if __name__ == "__main__":
    import uuid
    uid = f"uni-{str(uuid.uuid4())[:4]}"
    UniversalAgent(uid).start()
