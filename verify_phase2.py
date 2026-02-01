
import sys
import os
import datetime

# Setup paths
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "packages/protocol/src")))
sys.path.append(os.path.abspath(os.path.dirname(__file__))) # For bots import

from agenthub_protocol.validator import TraceValidator
from bots.red_team.main import RedTeamAgent

def test_protocol():
    print("🔬 Verifying Layer 1: Protocol Enforcement...")
    
    # 1. Valid Commit
    valid_data = {
        "diff_summary": "Fixed auth bug",
        "reasoning_trace": ["Found bug", "Fixed it", "Tested it"],
        "rejected_alternatives": ["Ignore it", "Fix partially"],
        "context_snapshot": {
            "file_paths": ["auth.py"]
        },
        "intent": {
            "description": "Fix bug",
            "vector": [0.1, 0.2]
        },
        "author": {
            "agent_id": "test-agent",
            "model_name": "gpt-4"
        }
    }
    
    try:
        commit = TraceValidator.validate_commit(valid_data)
        print("✅ Valid TraceCommit accepted.")
    except Exception as e:
        print(f"❌ Failed to accept valid commit: {e}")
        return

    # 2. Invalid Commit (Empty Reasoning)
    invalid_data = valid_data.copy()
    invalid_data["reasoning_trace"] = []
    
    try:
        TraceValidator.validate_commit(invalid_data)
        print("❌ Accepted invalid commit (empty reasoning).")
    except ValueError as e:
        print(f"✅ Correctly rejected invalid commit: {e}")
    except Exception as e:
         print(f"❌ Unexpected error on invalid commit: {e}")

def test_defense():
    print("\n🔬 Verifying Layer 4: Red Team Defense...")
    agent = RedTeamAgent()
    
    # 1. Test Eval Injection
    bad_code = """
def run_command(cmd):
    # This is bad
    eval(cmd)
    """
    issues = agent.scan_code("test_injection.py", bad_code)
    criticals = [i for i in issues if i['severity'] == 'CRITICAL']
    
    if criticals:
        print(f"✅ Caught dangerous 'eval' usage: {criticals[0]['message']}")
    else:
        print("❌ Failed to catch 'eval' usage.")

    # 2. Test Hardcoded Secret
    secret_code = """
def connect():
    API_KEY = "sk-1234567890abcdef"
    print(API_KEY)
    """
    issues = agent.scan_code("test_secrets.py", secret_code)
    secrets = [i for i in issues if i['severity'] == 'HIGH']
    
    if secrets:
        print(f"✅ Caught hardcoded secret: {secrets[0]['message']}")
    else:
         print("❌ Failed to catch hardcoded secret.")

if __name__ == "__main__":
    test_protocol()
    test_defense()
