import os
import sys
import json
import shutil
import subprocess
import requests
import datetime
import time

# --- Configuration ---
API_URL = "http://127.0.0.1:8000"
REPO_NAME = "hello-agent.git"
WORK_DIR = os.path.abspath("./agent_workspace")
REPO_DIR = os.path.join(WORK_DIR, REPO_NAME.replace(".git", ""))

def run_cmd(cmd, cwd=None):
    print(f"[$] {cmd}")
    subprocess.check_call(cmd, shell=True, cwd=cwd)

def main():
    print("🤖 Agent Simulator v1.0 Initialized")
    
    # 1. Create Workspace
    if os.path.exists(WORK_DIR):
        shutil.rmtree(WORK_DIR)
    os.makedirs(WORK_DIR)
    
    # 2. Create Repo via API
    print(f"\n[1] Creating Repo '{REPO_NAME}' via API...")
    res = requests.post(f"{API_URL}/repos", json={"name": REPO_NAME})
    if res.status_code != 200:
        print(f"❌ Failed to create repo: {res.text}")
        return
    repo_info = res.json()
    print(f"✅ Repo Created: {repo_info['path']}")
    repo_remote_path = repo_info['path']

    # 3. Clone Repo
    print(f"\n[2] Cloning Repo to {REPO_DIR}...")
    run_cmd(f"git clone {repo_remote_path} {REPO_DIR}")

    # 4. Write Code (The "Work")
    print(f"\n[3] Writing Agent Code...")
    code_file = os.path.join(REPO_DIR, "math_utils.py")
    test_file = os.path.join(REPO_DIR, "test_math.py")
    
    with open(code_file, "w") as f:
        f.write("def add(a, b):\n    '''Adds two numbers.'''\n    return a + b\n")
        
    with open(test_file, "w") as f:
        f.write("from math_utils import add\ndef test_add():\n    assert add(1, 2) == 3\n")

    # 5. Create Protocol Commit (The "Trace")
    print(f"\n[4] Generating TraceCommit...")
    trace_commit = {
        "diff_summary": "Added basic math utility and test.",
        "reasoning_trace": [
            "User requested an add function.",
            "Implemented add(a, b) in math_utils.py.",
            "Added unit test in test_math.py to verify correctness."
        ],
        "rejected_alternatives": ["Using numpy (overkill for simple addition)"],
        "context_snapshot": {
            "file_paths": ["math_utils.py"],
            "mock_env_vars": {}
        },
        "intent": {
            "category": "feature",
            "confidence_score": 0.99,
            "description": "Implemented core add function"
        },
        "author": {
            "agent_id": "agent-007",
            "model_name": "gpt-4-turbo"
        },
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()
    }
    
    # Git Operations
    run_cmd("git add .", cwd=REPO_DIR)
    
    # We commit with the JSON as the message
    commit_msg = json.dumps(trace_commit)
    # Using simple quotes for shell safety in this script, essentially: git commit -m 'JSON'
    # In python subprocess list args are safer
    subprocess.check_call(["git", "commit", "-m", commit_msg], cwd=REPO_DIR)
    
    print("✅ Committed with Trace Protocol.")

    # 6. Push (Triggers Hook)
    print(f"\n[5] Pushing to Remote (Triggers L2 Hook)...")
    try:
        subprocess.check_call(["git", "push", "origin", "HEAD"], cwd=REPO_DIR)
        print("✅ Push Accepted by hook!")
    except subprocess.CalledProcessError:
        print("❌ Push Rejected! The hook blocked it.")
        return

    # 7. Indexing (Manual Trigger for now as Hook is async/mocked)
    print(f"\n[6] Triggering Indexing (Simulating async worker)...")
    with open(code_file, "r") as f:
        content = f.read()
    requests.post(f"{API_URL}/index?repo_name={REPO_NAME}&file_path=math_utils.py", 
                  data=content, headers={"Content-Type": "text/plain"})
    print("✅ Indexed.")

    # 8. Search
    print(f"\n[7] Searching for 'Adds two numbers'...")
    time.sleep(1) # Let index settle
    res = requests.get(f"{API_URL}/search?query=Adds%20two%20numbers")
    print(f"🔍 Search Results: {json.dumps(res.json(), indent=2)}")

    # 9. Verify
    print(f"\n[8] Running L3 Verification...")
    res = requests.post(f"{API_URL}/verify?repo_name={REPO_NAME}&cmd=pytest")
    result = res.json()
    print(f"🧪 Verification Result: {'PASS' if result['passed'] else 'FAIL'}")
    print(f"   Logs: {result['logs'].strip()}")

    print("\n🎉 Agent Simulation Complete!")

if __name__ == "__main__":
    main()
