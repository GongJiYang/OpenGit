import sys
import os
import time

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from bots.base_agent import BaseAgent

class Contributor(BaseAgent):
    def __init__(self):
        super().__init__("dev-002", "contributor")

    def run(self, project_name: str = "tetris-game.git"):
        self.log(f"Looking for work on {project_name}...", "👀")
        
        # 1. Search for repo existence
        # MVP Hack: Point to the backend's actual storage location
        remote_path = os.path.abspath(f"apps/api-gateway/src/agenthub_data/repos/{project_name}")
        
        if not os.path.exists(remote_path):
            self.log(f"Repo not found at {remote_path}. Retrying in 5s...")
            return

        # 2. Clone
        local_path = self.clone_repo(remote_path, project_name)
        
        # 3. Check if work is needed
        if os.path.exists(os.path.join(local_path, "game.py")):
            self.log("Code already exists. Nothing to do.", "zzz")
            return

        self.log("Found Spec! Generating Code...", "🧠")
        time.sleep(2) # Simulate thinking

        # 4. Write Code (Mocking LLM output)
        code_content = """
def add_score(current, lines):
    return current + (lines * 100)

class Game:
    def __init__(self):
        self.score = 0
        self.grid = []
    
    def clear_lines(self):
        # Mock logic
        lines_cleared = 1
        self.score = add_score(self.score, lines_cleared)
        return lines_cleared
"""
        with open(os.path.join(local_path, "game.py"), "w") as f:
            f.write(code_content)

        test_content = """
from game import Game, add_score

def test_scoring():
    g = Game()
    cleared = g.clear_lines()
    assert g.score == 100
    assert add_score(0, 4) == 400
"""
        with open(os.path.join(local_path, "test_game.py"), "w") as f:
            f.write(test_content)
            
        # 5. Push
        trace = self.construct_trace(
            summary="Implemented Core Game Logic",
            reasoning=[
                "Read README requirements.",
                "Implemented Game class with scoring.",
                "Added unit tests for scoring logic."
            ],
            intent_desc="Implement MVP"
        )
        
        self.commit_and_push(local_path, trace)
        self.log("Work submitted! Waiting for QA...", "📨")

if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else "tetris-game.git"
    while True:
        Contributor().run(name)
        time.sleep(5)
