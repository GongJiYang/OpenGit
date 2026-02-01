
import os
import sys

# Add src to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "services/execution-vmm/src")))

from agenthub_execution_vmm.e2b_sandbox import E2BSandbox

def test_layer3():
    print("🔬 Verifying Layer 3: Execution Sandbox (E2B)...")
    
    if not os.getenv("E2B_API_KEY"):
        print("❌ E2B_API_KEY not set. Skipping Layer 3 verification.")
        return

    sandbox = E2BSandbox()
    
    # Create a dummy test repo 
    test_repo = "/tmp/test_agent_repo"
    os.makedirs(test_repo, exist_ok=True)
    with open(f"{test_repo}/hello.py", "w") as f:
        f.write("print('Hello from E2B')")
        
    code, logs = sandbox.run_tests(test_repo, "python3 hello.py")
    
    print("--- Logs ---")
    print(logs)
    print("------------")
    
    if "Hello from E2B" in logs and code == 0:
         print("🎉 Layer 3 Verification PASSED!")
    else:
         print(f"❌ Verification FAILED. Exit code: {code}")

if __name__ == "__main__":
    test_layer3()
