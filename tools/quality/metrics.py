"""Static source measurements; these are review signals, not correctness scores.

Only Python's standard library is required. Parsing never imports application
code, opens devices, creates Qt objects, or evaluates annotations. Physical span
is reported separately from executable statements so catalogs and shader strings
are not mistaken for functions with hundreds of control-flow responsibilities.
"""
from __future__ import annotations

import ast
import hashlib
import io
import tokenize
from collections import Counter
from pathlib import Path
from typing import Iterable

from tools.quality.runtime_sources import legacy_implementation_sources

FUNCTION_NODES = (ast.FunctionDef, ast.AsyncFunctionDef)
SCOPE_NODES = (*FUNCTION_NODES, ast.ClassDef, ast.Lambda)
CONTROL_NODES = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.With,
                 ast.AsyncWith, ast.Match)


def scoped_nodes(node: ast.AST) -> Iterable[ast.AST]:
    """Visit one lexical responsibility, excluding bodies of nested scopes."""
    for child in ast.iter_child_nodes(node):
        yield child
        if not isinstance(child, SCOPE_NODES):
            yield from scoped_nodes(child)


def attribute_path(node: ast.AST) -> str | None:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        return ".".join([node.id, *reversed(parts)])
    return None


def control_depth(node: ast.AST, depth: int = 0) -> int:
    children = [child for child in ast.iter_child_nodes(node)
                if not isinstance(child, SCOPE_NODES)]
    def child_depth(child: ast.AST) -> int:
        # Python encodes an elif chain as nested If nodes. It is one decision
        # level for review purposes, not dozens of indentation levels.
        is_elif = isinstance(node, ast.If) and node.orelse == [child] and isinstance(child, ast.If)
        return depth + (isinstance(child, CONTROL_NODES) and not is_elif)

    return max([depth, *(control_depth(child, child_depth(child)) for child in children)])


def function_metrics(node: ast.FunctionDef | ast.AsyncFunctionDef) -> dict:
    nodes = list(scoped_nodes(node))
    statements = [item for item in nodes if isinstance(item, ast.stmt)]
    # A docstring is documentation, not executable responsibility.
    statements = [item for item in statements if not (
        isinstance(item, ast.Expr) and isinstance(item.value, ast.Constant)
        and isinstance(item.value.value, str)
    )]
    decisions = sum(isinstance(item, (ast.If, ast.IfExp, ast.For, ast.AsyncFor,
                                      ast.While, ast.ExceptHandler, ast.comprehension))
                    for item in nodes)
    decisions += sum(len(item.values) - 1 for item in nodes if isinstance(item, ast.BoolOp))
    decisions += sum(len(item.cases) for item in nodes if isinstance(item, ast.Match))
    self_members, state_reads, state_writes, calls = set(), set(), set(), set()
    for item in nodes:
        if isinstance(item, ast.Call):
            target = attribute_path(item.func)
            if target:
                calls.add(target)
        if not isinstance(item, ast.Attribute):
            continue
        path = attribute_path(item)
        if not path:
            continue
        if path.startswith("self."):
            self_members.add(path.split(".")[1])
        state_prefix = next((prefix for prefix in ("self.state.", "state.")
                             if path.startswith(prefix)), None)
        if state_prefix:
            field = path[len(state_prefix):].split(".")[0]
            target = state_writes if isinstance(item.ctx, (ast.Store, ast.Del)) else state_reads
            target.add(field)
    args = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
    parameters = [arg.arg for arg in args if arg.arg not in {"self", "cls"}]
    return {
        "line": node.lineno,
        "source_span": (node.end_lineno or node.lineno) - node.lineno + 1,
        "statements": len(statements),
        "decisions": decisions,
        "max_control_depth": control_depth(node),
        "parameters": len(parameters),
        "variadic": bool(node.args.vararg or node.args.kwarg),
        "self_members": sorted(self_members),
        "state_reads": sorted(state_reads),
        "state_writes": sorted(state_writes),
        "call_targets": sorted(calls),
    }


def module_name(path: Path, root: Path) -> str:
    parts = list(path.relative_to(root).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def import_candidates(node: ast.Import | ast.ImportFrom, module: str,
                      is_package: bool) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    package = module.split(".") if is_package else module.split(".")[:-1]
    if node.level:
        prefix = package[:len(package) - node.level + 1]
        base = ".".join([*prefix, *([node.module] if node.module else [])])
    else:
        base = node.module or ""
    return [base, *(f"{base}.{alias.name}" for alias in node.names if alias.name != "*")]


def resolve_module(candidate: str, modules: set[str]) -> str | None:
    while candidate:
        if candidate in modules:
            return candidate
        candidate = candidate.rpartition(".")[0]
    return None


def is_type_checking(node: ast.AST) -> bool:
    return isinstance(node, ast.Name) and node.id == "TYPE_CHECKING" or (
        isinstance(node, ast.Attribute) and node.attr == "TYPE_CHECKING"
        and isinstance(node.value, ast.Name) and node.value.id == "typing"
    )


class ModuleVisitor(ast.NodeVisitor):
    def __init__(self, path: str, module: str, is_package: bool, modules: set[str]):
        self.path, self.module = path, module
        self.is_package, self.modules = is_package, modules
        self.scope: list[str] = []
        self.deferred = 0
        self.type_only = 0
        self.functions: dict[str, dict] = {}
        self.imports: list[dict] = []
        self.declared_imports: list[dict] = []
        self.findings: list[dict] = []

    def finding(self, rule: str, node: ast.AST, detail: str) -> None:
        self.findings.append({"path": self.path, "scope": ".".join(self.scope) or "<module>",
                              "rule": rule, "line": node.lineno, "detail": detail})

    def visit(self, node: ast.AST):
        body = getattr(node, "body", None)
        if isinstance(node, (*CONTROL_NODES, *FUNCTION_NODES, ast.ClassDef, ast.ExceptHandler)):
            declaration_only = isinstance(node, FUNCTION_NODES) and len(body or []) == 1 and (
                isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
                and body[0].value.value is Ellipsis
            )
            if isinstance(body, list) and body and body[0].lineno == node.lineno and not declaration_only:
                self.finding("inline-suite", node, "compound statement and body share a source line")
        return super().visit(node)

    def visit_If(self, node: ast.If) -> None:
        self.visit(node.test)
        type_only = is_type_checking(node.test)
        self.type_only += type_only
        for child in node.body:
            self.visit(child)
        self.type_only -= type_only
        for child in node.orelse:
            self.visit(child)

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.scope.append(node.name)
        key = ".".join(self.scope)
        # Duplicate definitions are separately reported, never silently omitted.
        if key in self.functions:
            key += f"@{node.lineno}"
        self.functions[key] = function_metrics(node)
        self.check_scope(node.body)
        self.deferred += 1
        self.generic_visit(node)
        self.deferred -= 1
        self.scope.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.scope.append(node.name)
        self.check_scope(node.body)
        self.generic_visit(node)
        self.scope.pop()

    def visit_Import(self, node: ast.Import | ast.ImportFrom) -> None:
        candidates = import_candidates(node, self.module, self.is_package)
        self.declared_imports.extend({"target": target, "line": node.lineno,
                                      "deferred": bool(self.deferred), "type_only": bool(self.type_only)}
                                     for target in sorted(set(candidates)) if target)
        # Avoid treating imported symbols as separate edges to the same module.
        targets = {resolve_module(name, self.modules) for name in candidates}
        for target in sorted(target for target in targets if target and target != self.module):
            self.imports.append({"target": target, "line": node.lineno,
                                 "deferred": bool(self.deferred), "type_only": bool(self.type_only)})
        if isinstance(node, ast.ImportFrom) and any(alias.name == "*" for alias in node.names):
            self.finding("wildcard-import", node, f"from {node.module or '.'} import *")

    visit_ImportFrom = visit_Import

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.type is None:
            self.finding("bare-except", node, "catches process-control exceptions as well as ordinary failures")
        self.generic_visit(node)

    def check_scope(self, body: list[ast.stmt]) -> None:
        definitions: dict[str, ast.AST] = {}
        for node in body:
            if not isinstance(node, (*FUNCTION_NODES, ast.ClassDef)):
                continue
            if isinstance(node, FUNCTION_NODES):
                decorators = [attribute_path(item.func if isinstance(item, ast.Call) else item)
                              for item in node.decorator_list]
                if any(name and (name.endswith(".setter") or name.endswith(".deleter")
                                 or name.split(".")[-1] == "overload") for name in decorators):
                    continue
            if node.name in definitions:
                self.finding("duplicate-definition", node, f"{node.name} shadows definition at line {definitions[node.name].lineno}")
            definitions[node.name] = node


def strongly_connected(graph: dict[str, set[str]]) -> list[list[str]]:
    """Deterministic Tarjan SCCs; only components containing a cycle are returned."""
    indices, low, stack, active, components = {}, {}, [], set(), []

    def visit(vertex: str) -> None:
        indices[vertex] = low[vertex] = len(indices)
        stack.append(vertex)
        active.add(vertex)
        for target in sorted(graph.get(vertex, ())):
            if target not in indices:
                visit(target)
                low[vertex] = min(low[vertex], low[target])
            elif target in active:
                low[vertex] = min(low[vertex], indices[target])
        if low[vertex] == indices[vertex]:
            component = []
            while True:
                member = stack.pop()
                active.remove(member)
                component.append(member)
                if member == vertex:
                    break
            if len(component) > 1 or vertex in graph.get(vertex, set()):
                components.append(sorted(component))

    for vertex in sorted(graph):
        if vertex not in indices:
            visit(vertex)
    return sorted(components)


def measure_tree(root: Path, directories: tuple[str, ...] = ("rm_client",)) -> dict:
    paths = sorted({path for directory in directories for path in (root / directory).rglob("*.py")
                    if not any(part.startswith(".") or part == "__pycache__"
                               for part in path.relative_to(root).parts)})
    if not paths:
        raise ValueError("No Python source files discovered in the requested source directories")
    modules = {module_name(path, root) for path in paths}
    results, syntax_errors = {}, []
    for path in paths:
        relative = path.relative_to(root).as_posix()
        name = module_name(path, root)
        try:
            source = path.read_text(encoding="utf-8-sig")
            tree = ast.parse(source, filename=relative)
        except (SyntaxError, UnicodeError) as exc:
            syntax_errors.append({"path": relative, "error": str(exc)})
            continue
        visitor = ModuleVisitor(relative, name, path.name == "__init__.py", modules)
        visitor.check_scope(tree.body)
        visitor.visit(tree)
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type == tokenize.OP and token.string == ";":
                visitor.findings.append({"path": relative, "scope": "<module>",
                                         "rule": "multiple-statements", "line": token.start[0],
                                         "detail": "semicolon separates statements"})
        results[name] = {"path": relative, "source_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
                         "source_lines": len(source.splitlines()),
                         "functions": visitor.functions, "imports": visitor.imports,
                         "declared_imports": visitor.declared_imports,
                         "findings": visitor.findings}
    # Keep historical implementation in all totals; label it rather than
    # pretending a filename move reduced its complexity or added runtime users.
    legacy = legacy_implementation_sources(root) if not syntax_errors else {}
    for module in results.values():
        module["source_role"] = ("historical-ui-implementation" if root / module["path"] in legacy
                                 else "current-source")
    runtime_graph = {name: {edge["target"] for edge in module["imports"]
                            if not edge["deferred"] and not edge["type_only"]}
                     for name, module in results.items()}
    all_graph = {name: {edge["target"] for edge in module["imports"] if not edge["type_only"]}
                 for name, module in results.items()}
    fan_in = Counter(target for targets in all_graph.values() for target in targets)
    for name, module in results.items():
        module["fan_out"] = len(all_graph[name])
        module["fan_in"] = fan_in[name]
    functions = [function for module in results.values() for function in module["functions"].values()]
    tree_digest = hashlib.sha256("\n".join(f"{module['path']}:{module['source_sha256']}"
                                              for module in results.values()).encode("utf-8")).hexdigest()
    return {"schema_version": 1, "source_directories": list(directories), "source_tree_sha256": tree_digest,
            "summary": {"files": len(paths), "parsed_files": len(results), "functions": len(functions),
                        "statements": sum(function["statements"] for function in functions),
                        "decisions": sum(function["decisions"] for function in functions),
                        "import_edges": sum(len(edges) for edges in all_graph.values()),
                        "historical_ui_modules": len(legacy),
                        "historical_ui_functions": sum(sum(name != "main" or module["path"] != "rm_client/app.py"
                                                          for name in module["functions"])
                                                       for module in results.values()
                                                       if module["source_role"] == "historical-ui-implementation")},
            "syntax_errors": syntax_errors, "modules": results,
            "runtime_cycles": strongly_connected(runtime_graph),
            "all_import_cycles": strongly_connected(all_graph)}
