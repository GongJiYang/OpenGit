import sys
import os
import time

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from bots.base_agent import BaseAgent

class Executor(BaseAgent):
    def __init__(self):
        super().__init__("qa-003", "executor")

    def run(self, project_name: str = "tetris-game.git"):
        self.log(f"Monitoring {project_name} for CI/CD...", "🛡️")
        
        # Simplification: We just force verify every loop to see current state
        # In real life, we would listen to a webhook.
        
        result = self.trigger_verify(project_name)
        
        if result.get("passed"):
            self.log(f"Build PASSED! Exit Code: {result['exit_code']}", "🟢")
        else:
            self.log(f"Build FAILED! Logs: {result.get('logs')}", "🔴")

if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else "tetris-game.git"
    while True:
        Executor().run(name)
        time.sleep(10)
