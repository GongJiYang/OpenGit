
import inspect
from e2b_code_interpreter import Sandbox

print("--- E2B Code Interpreter Debug ---")
print(f"Sandbox: {Sandbox}")
print(f"MRO: {Sandbox.mro()}")
try:
    sig = inspect.signature(Sandbox.__init__)
    print(f"Sig: {sig}")
except Exception as e:
    print(f"Sig Error: {e}")

    print("Trying Sandbox.create()...")
    s = Sandbox.create()
    print("Success create()")
    print(f"Instance dir: {dir(s)}")
    s.kill()
except Exception as e:
    print(f"Create failed: {e}")
