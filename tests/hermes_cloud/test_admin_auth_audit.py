from __future__ import annotations

import ast
from pathlib import Path


def _route_path_from_decorator(decorator: ast.AST) -> str | None:
    if not isinstance(decorator, ast.Call):
        return None
    if not isinstance(decorator.func, ast.Attribute):
        return None
    if not isinstance(decorator.func.value, ast.Name):
        return None
    if decorator.func.value.id != "app":
        return None
    if decorator.func.attr not in {"get", "post", "put", "patch", "delete"}:
        return None
    if not decorator.args:
        return None
    first = decorator.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value
    return None


def _func_calls_name(func: ast.FunctionDef | ast.AsyncFunctionDef, name: str) -> bool:
    for node in ast.walk(func):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == name:
                return True
    return False


def test_admin_routes_are_guarded():
    main_py = Path(__file__).resolve().parents[2] / "hermes_cloud" / "app" / "main.py"
    tree = ast.parse(main_py.read_text(encoding="utf-8"))

    create_app = next(
        (
            n
            for n in tree.body
            if isinstance(n, ast.FunctionDef) and n.name == "create_app"
        ),
        None,
    )
    assert create_app is not None, "create_app() not found in hermes_cloud/app/main.py"

    missing: list[tuple[str, str]] = []
    for node in create_app.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        route_paths = [
            p for p in (_route_path_from_decorator(d) for d in node.decorator_list) if p
        ]
        for path in route_paths:
            if path.startswith("/admin/"):
                if not _func_calls_name(node, "require_admin_auth"):
                    missing.append((path, node.name))
            elif path.startswith("/api/admin/"):
                guarded = (
                    _func_calls_name(node, "require_admin_auth")
                    or _func_calls_name(node, "_require_admin_module_view")
                )
                if not guarded:
                    missing.append((path, node.name))

    assert not missing, (
        "unguarded admin routes found: "
        + ", ".join(f"{path} ({fn})" for path, fn in missing)
    )
