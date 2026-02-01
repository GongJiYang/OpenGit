import sys
import os
import json
import subprocess
from typing import List, Tuple

# Ensure we can import the protocol package
# In a real deployment, this would be installed in the environment
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../packages/protocol/src")))

from agenthub_protocol import TraceCommit

def get_commit_message(commit_sha: str) -> str:
    """Read the raw commit message body."""
    cmd = ["git", "log", "-1", "--format=%B", commit_sha]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return result.stdout.strip()

def validate_push() -> None:
    """
    Standard Git Pre-Receive Hook.
    Reads (old_sha, new_sha, ref_name) from stdin.
    """
    print("🤖 AgentHub Guard: Inspecting incoming commits...", file=sys.stderr)
    
    # Read lines from stdin
    input_lines = sys.stdin.read().strip().splitlines()
    
    for line in input_lines:
        old_sha, new_sha, ref = line.split()
        
        # Skip creating a new branch or deleting one for MVP simplicity
        # (Real logic would check the whole range)
        if new_sha == "0000000000000000000000000000000000000000":
             continue # Delete branch
        
        # For new branch, check just the tip? Or all commits?
        # MVP: Check the HEAD commit of the push.
        
        msg = get_commit_message(new_sha)
        
        try:
            # 1. Attempt to parse as JSON
            data = json.loads(msg)
            
            # 2. Validate against Protocol
            # We construct a TraceCommit object. If it fails, Pydantic raises ValidationError.
            trace = TraceCommit(**data)
            
            print(f"✅ Protocol Verified: {trace.diff_summary}", file=sys.stderr)
            print(f"🧠 Reasoning Trace: {len(trace.reasoning_trace)} steps", file=sys.stderr)
            
        except json.JSONDecodeError:
            print(f"❌ REJECTED: Commit {new_sha[:7]} is not a valid JSON.", file=sys.stderr)
            print("   AgentHub requires all commits to be structured JSON conforming to TraceCommit Schema.", file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            print(f"❌ REJECTED: Commit {new_sha[:7]} violates AgentHub Protocol.", file=sys.stderr)
            print(f"   Error: {str(e)}", file=sys.stderr)
            sys.exit(1)

if __name__ == "__main__":
    validate_push()
