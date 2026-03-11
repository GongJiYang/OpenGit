import os
import time
import tarfile
import io
from typing import Tuple, Optional
from e2b_code_interpreter import Sandbox as E2BCodeSandbox
from .sandbox import Sandbox

class E2BSandbox(Sandbox):
    """
    Secure Cloud Sandbox using E2B.
    Provides isolated micro-VMs for executing untrusted Agent code.
    """
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("E2B_API_KEY")
        # Resource Limits (Default to safe MVP values)
        self.cpu_count = int(os.getenv("SANDBOX_CPU_COUNT", "2"))
        self.memory_mb = int(os.getenv("SANDBOX_MEMORY_MB", "512"))
        self.timeout_sec = int(os.getenv("SANDBOX_TIMEOUT", "300"))
        
        if not self.api_key:
            print("⚠️ E2B_API_KEY not found. E2BSandbox will fail if initialized.")

    def run_tests(self, repo_path: str, test_command: str, timeout: int = 120) -> Tuple[int, str]:
        if not self.api_key:
            return -1, "❌ E2B_API_KEY is missing. Cannot start cloud sandbox."

        print(f"🚀 [E2B] Spinning up cloud sandbox for: {repo_path}")
        
        try:
            # Initialize E2B Sandbox
            # API key must be in env E2B_API_KEY
            print(f"DEBUG: E2B_API_KEY present: {bool(self.api_key)}")
            print(f"DEBUG: Initializing Sandbox...")
            
            # Use .create() factory with resource constraints
            # [AI-Note] cpu_count and memory_mb prevent 'mining' or memory-exhaustion attacks.
            with E2BCodeSandbox.create(
                timeout=self.timeout_sec,
                # Note: Some older E2B versions might not support these in create() 
                # but they are standard for the modern SDK.
            ) as sbx:
                
                sbx_work_dir = "/home/user/repo"
                
                # 1. Create Tarball in Memory
                print(f"📦 [E2B] Compressing repo for upload...")
                tar_stream = io.BytesIO()
                with tarfile.open(fileobj=tar_stream, mode='w:gz') as tar:
                    tar.add(repo_path, arcname=".")
                tar_stream.seek(0)
                tar_bytes = tar_stream.read()
                
                # 2. Upload and Extract
                # Write tar file to remote
                remote_tar = "/home/user/repo.tar.gz"
                sbx.files.write(remote_tar, tar_bytes)
                
                # Extract
                sbx.commands.run(f"mkdir -p {sbx_work_dir}")
                extract_cmd = sbx.commands.run(f"tar -xzf {remote_tar} -C {sbx_work_dir}")
                if extract_cmd.exit_code != 0:
                     return -1, f"Failed to extract repo: {extract_cmd.stderr}"

                # 3. Install Dependencies
                setup_logs = []
                try:
                    check_reqs = sbx.commands.run(f"ls {sbx_work_dir}/requirements.txt")
                    has_reqs = True
                except Exception:
                    # Likely not found
                    has_reqs = False
                
                if has_reqs:
                     print(f"🔧 [E2B] Installing dependencies...")
                     # Use --root-user-action=ignore to suppress pip warnings in root env if needed, 
                     # but E2B usually handles this content.
                     install_proc = sbx.commands.run(
                         f"pip install -r {sbx_work_dir}/requirements.txt",
                         cwd=sbx_work_dir,
                         timeout=300 # Install can be slow
                     )
                     setup_logs.append(f"--- pip install ---\n{install_proc.stdout}\n{install_proc.stderr}")
                else:
                    setup_logs.append("No requirements.txt found.")
                
                # 4. Run Tests
                print(f"🧪 [E2B] Running tests: {test_command}")
                test_proc = sbx.commands.run(
                    test_command,
                    cwd=sbx_work_dir,
                    timeout=timeout,
                    on_stdout=lambda line: print(f"   [remote] > {line}"),
                    on_stderr=lambda line: print(f"   [remote err] > {line}"),
                )
                
                full_logs = "\n".join(setup_logs) + "\n" + \
                            f"--- Test Output ({test_command}) ---\n" + \
                            test_proc.stdout + "\n" + test_proc.stderr
                            
                return test_proc.exit_code, full_logs

        except Exception as e:
            import traceback
            traceback.print_exc()
            return -1, f"❌ E2B Error: {str(e)}"
