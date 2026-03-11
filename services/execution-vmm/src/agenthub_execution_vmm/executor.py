from typing import Dict, Optional
from .sandbox import Sandbox

class SessionManager:
    """
    Manages persistent sandbox sessions for Agents.
    Maps (agent_id, task_id) to a persistent VM session.
    """
    def __init__(self, sandbox: Sandbox):
        self.sandbox = sandbox
        # key: f"{agent_id}:{task_id}", value: session_id
        self._sessions: Dict[str, str] = {}

    def get_or_create_session(self, agent_id: str, task_id: str, repo_path: str) -> str:
        """Retrieve an existing sandbox session or spin up a new one."""
        key = f"{agent_id}:{task_id}"
        if key in self._sessions:
             # In a real system, we'd check if the session is still active
             return self._sessions[key]
        
        print(f"🏗️ [Executor] Creating isolated drafting sandbox for {agent_id} on task {task_id}")
        session_id = self.sandbox.create_session(repo_path)
        self._sessions[key] = session_id
        return session_id

    def execute(self, agent_id: str, task_id: str, command: str) -> str:
        """Execute a command in the agent's dedicated session."""
        key = f"{agent_id}:{task_id}"
        if key not in self._sessions:
            return "❌ No active session found for this task. Initialize it first."
        
        session_id = self._sessions[key]
        exit_code, output = self.sandbox.run_command(session_id, command)
        return output

    def close_session(self, agent_id: str, task_id: str):
        """Cleanup the sandbox for this specific task."""
        key = f"{agent_id}:{task_id}"
        if key in self._sessions:
            session_id = self._sessions.pop(key)
            self.sandbox.close_session(session_id)
            print(f"🧹 [Executor] Closed sandbox session for {agent_id}")
