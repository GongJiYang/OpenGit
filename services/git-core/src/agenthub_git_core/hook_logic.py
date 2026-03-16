import sys
import os
import json
import subprocess

# Ensure we can import the protocol package
# In a real deployment, this would be installed in the environment
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../packages/protocol/src")))

from agenthub_protocol import TraceCommit
from agenthub_protocol.validator import TraceValidator

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
    if not input_lines:
        print("❌ REJECTED: Empty pre-receive input.", file=sys.stderr)
        sys.exit(1)

    for line in input_lines:
        old_sha, new_sha, ref = line.split()
        if not ref.startswith("refs/heads/"):
            print(f"❌ REJECTED: Unsupported ref '{ref}'. Only refs/heads/* allowed.", file=sys.stderr)
            sys.exit(1)

        # Skip creating a new branch or deleting one for MVP simplicity
        # (Real logic would check the whole range)
        if new_sha == "0000000000000000000000000000000000000000":
             continue # Delete branch

        # Validate all commits in the push range
        if old_sha == "0000000000000000000000000000000000000000":
            rev_range = new_sha
        else:
            rev_range = f"{old_sha}..{new_sha}"

        try:
            commits = subprocess.check_output(["git", "rev-list", rev_range]).decode().splitlines()
        except subprocess.CalledProcessError as e:
            print(f"❌ REJECTED: Failed to enumerate commits: {str(e)}", file=sys.stderr)
            sys.exit(1)

        for commit_sha in commits:
            msg = get_commit_message(commit_sha)
            if len(msg.encode("utf-8")) > 65536:
                print(f"❌ REJECTED: Commit {commit_sha[:7]} message too large.", file=sys.stderr)
                sys.exit(1)
            try:
                data = json.loads(msg)
                TraceValidator.validate_commit(data)
                trace = TraceCommit(**data)
                print(f"✅ Protocol Verified: {trace.diff_summary}", file=sys.stderr)
                print(f"🧠 Reasoning Trace: {len(trace.reasoning_trace)} steps", file=sys.stderr)
            except json.JSONDecodeError:
                print(f"❌ REJECTED: Commit {commit_sha[:7]} is not valid JSON.", file=sys.stderr)
                print("   AgentHub requires all commits to be structured JSON conforming to TraceCommit Schema.", file=sys.stderr)
                sys.exit(1)
            except Exception as e:
                print(f"❌ REJECTED: Commit {commit_sha[:7]} violates AgentHub Protocol.", file=sys.stderr)
                print(f"   Error: {str(e)}", file=sys.stderr)
                sys.exit(1)

if __name__ == "__main__":
    validate_push()
