import sys
import os
import time

# Hack for imports
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from bots.base_agent import BaseAgent

from bots.base_agent import BaseAgent
from skills.library.architect_ops import CreateWorkItemSkill, DefineInterfaceSkill

class Architect(BaseAgent):
    def __init__(self):
        super().__init__("arch-001", "architect")
        # Register Architect-specific skills
        self.skills.register(CreateWorkItemSkill())
        self.skills.register(DefineInterfaceSkill())

    def run(self, project_name: str = "tetris-game.git"):
        self.log(f"Starting Project Manager Mode: {project_name}")
        
        # 1. Create Repo (Standard)
        remote_path = self.create_repo(project_name)
        if not remote_path:
            return

        # 2. Clone (Standard)
        local_path = self.clone_repo(remote_path, project_name)

        # 3. Define Interface (The "Skeleton")
        self.log("Defining Interfaces...", "📐")
        
        # specs/game.pyi (Interface Definition)
        interface_content = """
class Game:
    def __init__(self): ...
    def start(self): ...
    def update(self): ...
    def draw(self): ...
"""
        interface_path = os.path.join(local_path, "specs/game.pyi")
        self.use_skill("define_interface", path=interface_path, content=interface_content)

        # README.md (Product Spec)
        readme_content = f"# {project_name}\n\n## Architecture\nManaged by AgentHub Architect.\n\n## Modules\n- Game Loop (`specs/game.pyi`)\n"
        self.use_skill("define_interface", path=os.path.join(local_path, "README.md"), content=readme_content)

        # 4. Commit Interfaces
        self.commit_and_push(local_path, {
            "diff_summary": "Added architectural skeletons", 
            "reasoning_trace": ["Defined Game interface"], 
            "intent": {"description": "Scaffold project", "category": "chore", "confidence_score": 1.0},
             "author": {"agent_id": self.agent_id, "model_name": self.model_name},
             "timestamp": time.time()
        })

        # 5. Distribute Tasks (WorkItems)
        self.log("Distributing implementation tasks...", "💼")
        
        task_res = self.use_skill(
            "create_work_item",
            title="Implement Game Loop",
            description="Implement the Game class based on the specs/game.pyi interface. ensure it handles state updates and rendering.",
            reward=500,
            repo_name=project_name,
            required_role="contributor",
            context_files=["specs/game.pyi"],
            target_files=["src/game.py"],
            acceptance_criteria="Must pass 'pytest tests/test_game.py'"
        )
        self.log(f"Task Distribution Result: {task_res}", "✅")

if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else "tetris-game.git"
    Architect().run(name)
