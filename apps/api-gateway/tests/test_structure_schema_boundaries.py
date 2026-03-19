from pathlib import Path
import ast


ROUTERS_ROOT = Path(__file__).resolve().parents[1] / "src"
ROUTERS_GLOB = "**/routers/*.py"
ALLOWED_INLINE_SQLMODEL_SCHEMAS = {
    # Transitional: still inline in router; can be migrated later
    "apps/api-gateway/src/agent_auth/routers/platform.py:UserUpdate",
}


def _rel_path(path: Path) -> str:
    parts = path.resolve().parts
    try:
        idx = parts.index("apps")
        return str(Path(*parts[idx:]))
    except ValueError:
        return str(path)


def _router_files():
    return [p for p in ROUTERS_ROOT.glob(ROUTERS_GLOB) if p.is_file()]


def _class_bases(node: ast.ClassDef) -> list[str]:
    bases: list[str] = []
    for base in node.bases:
        if isinstance(base, ast.Name):
            bases.append(base.id)
        elif isinstance(base, ast.Attribute):
            bases.append(base.attr)
    return bases


def test_routers_do_not_define_inline_basemodel_schemas():
    violations: list[str] = []

    for file_path in _router_files():
        source = file_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        rel = _rel_path(file_path)

        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            bases = _class_bases(node)
            if "BaseModel" in bases:
                violations.append(f"{rel}:{node.name}")

    assert not violations, (
        "Inline BaseModel API schemas found in router modules; move them to src/schemas/. "
        f"Violations: {violations}"
    )


def test_routers_do_not_define_inline_sqlmodel_request_response_schemas():
    violations: list[str] = []

    for file_path in _router_files():
        source = file_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        rel = _rel_path(file_path)

        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            bases = _class_bases(node)
            if "SQLModel" not in bases:
                continue

            marker = f"{rel}:{node.name}"
            if marker in ALLOWED_INLINE_SQLMODEL_SCHEMAS:
                continue
            violations.append(marker)

    assert not violations, (
        "Inline SQLModel request/response schemas found in router modules. "
        "Move API I/O schemas to src/schemas/ (keep ORM models in models/). "
        f"Violations: {violations}"
    )
