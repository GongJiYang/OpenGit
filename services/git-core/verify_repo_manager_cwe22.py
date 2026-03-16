import os
import shutil
from agenthub_git_core.repo_manager import RepoManager

def test_repo_manager_cwe22_fix():
    print("🛡️ Testing RepoManager CWE-22 (Arbitrary Directory Deletion) Fix...")

    # Setup test store
    test_store = "./test_git_store_cwe22"
    if os.path.exists(test_store):
        shutil.rmtree(test_store)
    os.makedirs(test_store)

    # Create a "victim" directory outside the store
    victim_dir = "./victim_dir_to_delete"
    if os.path.exists(victim_dir):
        shutil.rmtree(victim_dir)
    os.makedirs(victim_dir)

    mgr = RepoManager(test_store)

    # 1. Attempt to delete victim_dir via create_repo
    # The relative path from test_store to victim_dir is ../victim_dir_to_delete
    malicious_repo_name = "../victim_dir_to_delete"

    print(f"   Attempting to 'create' repo with name: {malicious_repo_name}")
    try:
        mgr.create_repo(malicious_repo_name)
        print("   ❌ Failure: RepoManager allowed path traversal!")
    except ValueError as e:
        print(f"   ✅ Success: Blocked with error: {e}")
    except Exception as e:
        print(f"   ❓ Caught unexpected exception: {type(e).__name__}: {e}")

    # Check if victim_dir still exists
    if os.path.exists(victim_dir):
        print("   ✅ Success: victim_dir still exists.")
    else:
        print("   ❌ Failure: victim_dir was DELETED!")

    # Cleanup
    shutil.rmtree(test_store)
    if os.path.exists(victim_dir):
        shutil.rmtree(victim_dir)

if __name__ == "__main__":
    test_repo_manager_cwe22_fix()
