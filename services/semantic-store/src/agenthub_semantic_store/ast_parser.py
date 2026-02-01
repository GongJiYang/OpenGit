import ast
from typing import List, Dict, Any
from dataclasses import dataclass

@dataclass
class CodeChunk:
    name: str
    type: str  # "function", "class", "async_function"
    code: str
    start_line: int
    end_line: int
    docstring: str

class PythonASTParser:
    """
    Parses Python source code into semantic chunks (functions, classes).
    This allows Agents to search for 'def login' instead of just file matches.
    """
    
    def parse(self, source_code: str) -> List[CodeChunk]:
        chunks = []
        try:
            tree = ast.parse(source_code)
        except SyntaxError:
            # If code is broken, we can't parse semantics, but we can't crash.
            # In a real system, we'd log this or handle partial parsing.
            return []

        lines = source_code.splitlines()

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                # Extract the source segment
                # AST line numbers are 1-based
                start = node.lineno - 1
                end = node.end_lineno # inclusive, 1-based
                
                # Handling generic decorators would need more logic to include them in the snippet
                # For MVP, we extract the body range proper.
                
                chunk_code = "\n".join(lines[start:end])
                
                docstring = ast.get_docstring(node) or ""
                
                node_type = "function"
                if isinstance(node, ast.AsyncFunctionDef):
                    node_type = "async_function"
                elif isinstance(node, ast.ClassDef):
                    node_type = "class"
                
                chunks.append(CodeChunk(
                    name=node.name,
                    type=node_type,
                    code=chunk_code,
                    start_line=node.lineno,
                    end_line=node.end_lineno,
                    docstring=docstring
                ))
                
        return chunks

if __name__ == "__main__":
    # Test
    code = """
def hello_world():
    '''Says hello'''
    print("hello")

class Agent:
    def run(self):
        pass
"""
    parser = PythonASTParser()
    print(parser.parse(code))
