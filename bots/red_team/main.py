import ast
import os
from typing import List, Dict, Any

# Adjust import based on where BaseAgent is located relative to bots/red-team
# Assuming sys.path is set correctly at runtime or we use relative imports if package structure allows.
# For simplicity in this mono-repo structure:
# from bots.base_agent import BaseAgent 

class Vulnerability(dict):
    def __init__(self, file: str, line: int, severity: str, message: str):
        super().__init__(file=file, line=line, severity=severity, message=message)

class RedTeamAgent:
    """
    Automated Security Auditor.
    Scans code for obvious security flaws before they hit production.
    """
    
    def __init__(self):
        self.role = "red-team"

    def scan_code(self, file_path: str, code: str) -> List[Vulnerability]:
        """
        Static Analysis using AST.
        """
        issues = []
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return [Vulnerability(file_path, 0, "ERROR", "Syntax Error - Cannot parse code")]

        for node in ast.walk(tree):
            # 1. Check for 'eval' or 'exec'
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    if node.func.id in ['eval', 'exec']:
                        issues.append(Vulnerability(
                            file_path, 
                            node.lineno, 
                            "CRITICAL", 
                            f"Use of dangerous function '{node.func.id}' detected."
                        ))
            
            # 2. Check for hardcoded secrets (Heuristic: caps var name contains KEY/SECRET/TOKEN and string value)
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        name = target.id.upper()
                        if any(x in name for x in ['KEY', 'SECRET', 'TOKEN', 'PASSWORD']):
                            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                                val = node.value.value
                                if len(val) > 8 and " " not in val: # heuristics for an actual key
                                    issues.append(Vulnerability(
                                        file_path,
                                        node.lineno,
                                        "HIGH",
                                        f"Possible hardcoded secret in '{target.id}'"
                                    ))
        
        return issues

    def audit_repo(self, repo_path: str) -> List[Vulnerability]:
        all_issues = []
        print(f"🕵️‍♀️ [RedTeam] Scanning repo: {repo_path}")
        
        for root, dirs, files in os.walk(repo_path):
            for file in files:
                if file.endswith(".py"):
                    full_path = os.path.join(root, file)
                    try:
                        with open(full_path, "r", encoding="utf-8") as f:
                            content = f.read()
                        
                        file_issues = self.scan_code(full_path, content)
                        all_issues.extend(file_issues)
                    except Exception as e:
                        print(f"Failed to scan {file}: {e}")
        
        if all_issues:
            print(f"🚨 [RedTeam] Found {len(all_issues)} issues!")
        else:
            print("✅ [RedTeam] No obvious issues found.")
            
        return all_issues
