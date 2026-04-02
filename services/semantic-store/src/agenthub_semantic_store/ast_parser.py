import ast
import os
import re
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class CodeChunk:
    name: str
    type: str  # function/class/interface/module/doc/config_section/file_window
    code: str
    start_line: int
    end_line: int
    docstring: str


class PythonASTParser:
    """
    Parses Python source code into semantic chunks (functions, classes).
    """

    def parse(self, source_code: str, file_path: Optional[str] = None) -> List[CodeChunk]:
        del file_path
        chunks = []
        try:
            tree = ast.parse(source_code)
        except SyntaxError:
            return []

        lines = source_code.splitlines()

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                start = node.lineno - 1
                end = node.end_lineno
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
                    docstring=docstring,
                ))

        return chunks


class RegexLanguageParser:
    def __init__(self, patterns: List[tuple[str, str]]):
        self.patterns = [(chunk_type, re.compile(pattern, re.MULTILINE)) for chunk_type, pattern in patterns]

    def parse(self, source_code: str, file_path: Optional[str] = None) -> List[CodeChunk]:
        lines = source_code.splitlines()
        chunks: List[CodeChunk] = []
        seen = set()

        for chunk_type, pattern in self.patterns:
            for match in pattern.finditer(source_code):
                name = (match.groupdict().get("name") or "anonymous").strip() or "anonymous"
                start_line = source_code.count("\n", 0, match.start()) + 1
                end_line = self._infer_end_line(lines, start_line)
                key = (chunk_type, name, start_line)
                if key in seen:
                    continue
                seen.add(key)
                chunks.append(CodeChunk(
                    name=name,
                    type=chunk_type,
                    code="\n".join(lines[start_line - 1:end_line]),
                    start_line=start_line,
                    end_line=end_line,
                    docstring="",
                ))

        chunks.sort(key=lambda item: (item.start_line, item.name))
        return chunks

    @staticmethod
    def _infer_end_line(lines: List[str], start_line: int, max_span: int = 80) -> int:
        end_line = min(len(lines), start_line + max_span - 1)
        for idx in range(start_line, min(len(lines), start_line + max_span)):
            line = lines[idx].strip()
            if not line:
                return idx
        return end_line


class ConfigSectionParser:
    SECTION_RE = re.compile(r"^(?P<indent>\s*)(?P<name>[A-Za-z0-9_.\-\"']+)\s*:\s*(?:#.*)?$", re.MULTILINE)

    def parse(self, source_code: str, file_path: Optional[str] = None) -> List[CodeChunk]:
        del file_path
        lines = source_code.splitlines()
        chunks: List[CodeChunk] = []
        for match in self.SECTION_RE.finditer(source_code):
            start_line = source_code.count("\n", 0, match.start()) + 1
            indent = len(match.group("indent") or "")
            name = match.group("name").strip('"\'')
            end_line = self._infer_section_end(lines, start_line, indent)
            chunks.append(CodeChunk(
                name=name,
                type="config_section",
                code="\n".join(lines[start_line - 1:end_line]),
                start_line=start_line,
                end_line=end_line,
                docstring="",
            ))
        return chunks

    @staticmethod
    def _infer_section_end(lines: List[str], start_line: int, indent: int) -> int:
        end_line = len(lines)
        for idx in range(start_line, len(lines)):
            line = lines[idx]
            stripped = line.strip()
            if not stripped:
                continue
            current_indent = len(line) - len(line.lstrip(" "))
            if current_indent <= indent and not line.lstrip().startswith("-"):
                return idx
        return end_line


class WindowedTextParser:
    def __init__(self, window_lines: int = 60):
        self.window_lines = max(20, window_lines)

    def parse(self, source_code: str, file_path: Optional[str] = None) -> List[CodeChunk]:
        lines = source_code.splitlines()
        if not lines:
            return []
        base_name = os.path.basename(file_path or "file")
        chunks: List[CodeChunk] = []
        for start_idx in range(0, len(lines), self.window_lines):
            end_idx = min(len(lines), start_idx + self.window_lines)
            chunks.append(CodeChunk(
                name=f"{base_name}:{start_idx + 1}-{end_idx}",
                type="file_window",
                code="\n".join(lines[start_idx:end_idx]),
                start_line=start_idx + 1,
                end_line=end_idx,
                docstring="",
            ))
        return chunks


class SemanticParser:
    TS_PATTERNS = [
        ("function", r"^\s*export\s+function\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\("),
        ("function", r"^\s*function\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\("),
        ("async_function", r"^\s*export\s+async\s+function\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\("),
        ("class", r"^\s*export\s+class\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\b"),
        ("class", r"^\s*class\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\b"),
        ("interface", r"^\s*export\s+interface\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\b"),
        ("interface", r"^\s*interface\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\b"),
        ("function", r"^\s*export\s+const\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:async\s*)?\("),
        ("function", r"^\s*const\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:async\s*)?\("),
    ]
    GO_PATTERNS = [
        ("function", r"^\s*func\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\("),
        ("method", r"^\s*func\s*\([^\)]*\)\s*(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\("),
        ("class", r"^\s*type\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s+struct\b"),
        ("interface", r"^\s*type\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s+interface\b"),
    ]

    def __init__(self):
        self.python = PythonASTParser()
        self.ts = RegexLanguageParser(self.TS_PATTERNS)
        self.go = RegexLanguageParser(self.GO_PATTERNS)
        self.config = ConfigSectionParser()
        self.fallback = WindowedTextParser()

    def parse(self, source_code: str, file_path: Optional[str] = None) -> List[CodeChunk]:
        extension = os.path.splitext((file_path or "").lower())[1]

        if extension == ".py":
            chunks = self.python.parse(source_code, file_path=file_path)
            return chunks or self.fallback.parse(source_code, file_path=file_path)

        if extension in {".ts", ".tsx", ".js", ".jsx"}:
            chunks = self.ts.parse(source_code, file_path=file_path)
            return chunks or self.fallback.parse(source_code, file_path=file_path)

        if extension == ".go":
            chunks = self.go.parse(source_code, file_path=file_path)
            return chunks or self.fallback.parse(source_code, file_path=file_path)

        if extension in {".yml", ".yaml", ".json", ".toml", ".ini", ".cfg"}:
            chunks = self.config.parse(source_code, file_path=file_path)
            return chunks or self.fallback.parse(source_code, file_path=file_path)

        return self.fallback.parse(source_code, file_path=file_path)


if __name__ == "__main__":
    code = """
def hello_world():
    '''Says hello'''
    print("hello")

class Agent:
    def run(self):
        pass
"""
    parser = SemanticParser()
    print(parser.parse(code, file_path="example.py"))
