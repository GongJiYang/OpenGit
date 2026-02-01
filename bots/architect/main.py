import sys
import os
import time

# Hack for imports
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from bots.base_agent import BaseAgent

class Architect(BaseAgent):
    def __init__(self):
        super().__init__("arch-001", "architect")

    def run(self, project_name: str = "tetris-game.git"):
        self.log(f"Starting Project: {project_name}")
        
        # 1. Create Repo
        remote_path = self.create_repo(project_name)
        if not remote_path:
            return

        # 2. Clone
        local_path = self.clone_repo(remote_path, project_name)

        # 3. Create Spec (README.md)
        self.log("Drafting Product Spec...", "📝")
        spec_content = f"""# {project_name.replace('.git', '').title()}

## Overview
A simple implementation of the classic game within a single Python file.

## Requirements
- Use `pygame` library (or standard library).
- Must implement basic game loop.
- Must have a unit test file.
"""
        with open(os.path.join(local_path, "README.md"), "w") as f:
            f.write(spec_content)

        # 4. Commit Spec
        trace = self.construct_trace(
            summary="Initial Commit: Added Product Spec",
            reasoning=["Project reset.", "Defined requirements in README."],
            intent_desc="Initialize project structure"
        )
        
        self.commit_and_push(local_path, trace)
        self.log("Project Initialized! Waiting for Contributors...", "zz")

if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else "tetris-game.git"
    Architect().run(name)
