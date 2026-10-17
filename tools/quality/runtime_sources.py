"""Narrow, source-backed classification of the retained historical Tk client.

This is not a general reachability analyzer. It recognizes the established Qt
entrypoint and the explicitly retained compatibility implementation. All other
source stays in the current-runtime evidence search, even if apparently unused.
"""
from __future__ import annotations

import ast
from pathlib import Path

QT_MODULES = {(1, "ui.polar_app"), (0, "rm_client.ui.polar_app")}


def without_docstring(body: list[ast.stmt]) -> list[ast.stmt]:
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        return body[1:]
    return body


def qt_forwarder(tree: ast.Module) -> bool:
    """Require an exact main -> imported canonical Qt main forwarding body."""
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main"]
    if len(functions) != 1:
        return False
    body = without_docstring(functions[0].body)
    if len(body) != 2 or not isinstance(body[0], ast.ImportFrom):
        return False
    imported, invocation = body
    if (imported.level, imported.module) not in QT_MODULES or len(imported.names) != 1:
        return False
    alias = imported.names[0]
    if alias.name != "main" or not isinstance(invocation, (ast.Expr, ast.Return)):
        return False
    call = invocation.value
    return isinstance(call, ast.Call) and isinstance(call.func, ast.Name) \
        and call.func.id == (alias.asname or "main") and not call.args and not call.keywords


def has_legacy_class(tree: ast.Module) -> bool:
    return any(isinstance(node, ast.ClassDef) and node.name == "RMClientApp" for node in tree.body)


def lazy_legacy_export(tree: ast.Module) -> bool:
    """Verify the compatibility module is referenced only in __getattr__."""
    locations = []
    lazy_calls = []
    for owner in tree.body:
        for node in ast.walk(owner):
            in_getattr = isinstance(owner, ast.FunctionDef) and owner.name == "__getattr__"
            if isinstance(node, ast.Constant) and node.value in {".legacy_tk_app", "rm_client.legacy_tk_app"}:
                locations.append(in_getattr)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "import_module":
                if node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value in {
                    ".legacy_tk_app", "rm_client.legacy_tk_app",
                }:
                    lazy_calls.append(in_getattr)
            if isinstance(node, ast.ImportFrom) and node.module and "legacy_tk_app" in node.module:
                return False
            if isinstance(node, ast.Import) and any("legacy_tk_app" in alias.name for alias in node.names):
                return False
    return bool(locations) and all(locations) and bool(lazy_calls) and all(lazy_calls)


def has_other_legacy_consumer(root: Path) -> bool:
    """Conservatively retain runtime evidence when other source names legacy UI."""
    for path in (root / "rm_client").rglob("*.py"):
        if path in {root / "rm_client/app.py", root / "rm_client/legacy_tk_app.py"}:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "RMClientApp":
                return True
            if isinstance(node, ast.ImportFrom):
                if "legacy_tk_app" in (node.module or "").split("."):
                    return True
                if any(alias.name in {"legacy_tk_app", "RMClientApp"} for alias in node.names):
                    return True
            if isinstance(node, ast.Import) and any("legacy_tk_app" in alias.name.split(".") for alias in node.names):
                return True
            if isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant):
                if node.args[0].value in {".legacy_tk_app", "rm_client.legacy_tk_app"}:
                    return True
    return False


def legacy_implementation_sources(root: Path) -> dict[Path, str]:
    """Return only verified historical implementation files, with the evidence.

    The old mixed app.py and the moved legacy_tk_app.py receive the same
    implementation classification. Relocation therefore cannot masquerade as
    adding consumers or fixing configuration orphan counts.
    """
    app = root / "rm_client/app.py"
    if not app.is_file():
        return {}
    tree = ast.parse(app.read_text(encoding="utf-8-sig"), filename=str(app))
    if not qt_forwarder(tree) or has_other_legacy_consumer(root):
        return {}
    proof = "rm_client.app.main forwards directly to rm_client.ui.polar_app.main"
    if has_legacy_class(tree):
        return {app: proof + "; this file also retains the historical RMClientApp implementation"}
    legacy = root / "rm_client/legacy_tk_app.py"
    if legacy.is_file() and lazy_legacy_export(tree):
        legacy_tree = ast.parse(legacy.read_text(encoding="utf-8-sig"), filename=str(legacy))
        if has_legacy_class(legacy_tree):
            return {legacy: proof + "; the facade references historical symbols only inside its explicit compatibility __getattr__"}
    return {}
