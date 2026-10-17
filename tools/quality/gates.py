"""Small, explicit ratchets for reviewable architectural debt.

Budgets are per symbol and metric, never file-line or function-line limits.
Retiring a symbol retires its budget; moving its debt is visible in the report.
Reports and baseline files are not rewritten by a check.
"""
from __future__ import annotations

from collections import Counter, deque
import json
from pathlib import Path

SCHEMA_VERSION = 2
REQUIRED_BASELINE_KEYS = {"schema_version", "source_revision", "rationale", "lint_debt",
                          "runtime_cycles", "all_import_cycles", "function_budgets"}
LOWER_LAYER_PREFIXES = ("rm_client.backend", "rm_client.configuration")
LOWER_LAYER_MODULES = frozenset({"rm_client.model", "rm_client.module_catalog",
                                "rm_client.module_settings", "rm_client.referee_profiles",
                                "rm_client.economy_values", "rm_client.crosshair_profile", "rm_client.config_store",
                                "rm_client.lifecycle", "rm_client.emergency_actions",
                                "rm_client.qualification_observations",
                                "rm_client.runtime_paths", "rm_client.state_overlay"})
GUI_AND_BACKEND_PREFIXES = ("rm_client.backend", "PySide6", "PySide2", "PyQt6", "PyQt5",
                            "tkinter", "Tkinter")
PURE_DOMAIN_FORBIDDEN_PREFIXES = (*GUI_AND_BACKEND_PREFIXES, "rm_client.ui")
# Policies describe reviewed dependencies, not directory-based purity. Pure
# owners hosted under ui may legitimately depend on other pure UI siblings.
PURE_DOMAIN_POLICIES = {
    "rm_client.referee_profiles": PURE_DOMAIN_FORBIDDEN_PREFIXES,
    "rm_client.economy_values": PURE_DOMAIN_FORBIDDEN_PREFIXES,
    "rm_client.crosshair_profile": (*PURE_DOMAIN_FORBIDDEN_PREFIXES, "rm_client.model",
                                   "rm_client.cs2_crosshair", "rm_client.serial_ports"),
    "rm_client.ui.preference_draft": (*GUI_AND_BACKEND_PREFIXES, "rm_client.model"),
    "rm_client.ui.polar.startup_activation": (*GUI_AND_BACKEND_PREFIXES,
                                            "rm_client.ui.polar_app", "rm_client.config_store",
                                            "rm_client.configuration"),
}
PURE_DOMAIN_MODULES = frozenset(PURE_DOMAIN_POLICIES)



RESPONSIBILITY_METRICS = frozenset({"statements", "decisions", "max_control_depth"})


def is_lower_layer(module: str) -> bool:
    return module in LOWER_LAYER_MODULES or any(module == prefix or module.startswith(prefix + ".")
                                               for prefix in LOWER_LAYER_PREFIXES)


def debt_key(finding: dict) -> str:
    return f"{finding['path']}:{finding['scope']}:{finding['rule']}"


def lint_debt(report: dict) -> dict[str, int]:
    return dict(sorted(Counter(debt_key(finding) for module in report["modules"].values()
                               for finding in module["findings"]).items()))


def load_baseline(path: Path) -> dict:
    baseline = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(baseline, dict) or not REQUIRED_BASELINE_KEYS <= baseline.keys():
        raise ValueError("Quality baseline is incomplete; expected an explicit reviewed schema")
    if type(baseline["schema_version"]) is not int or baseline["schema_version"] != SCHEMA_VERSION:
        raise ValueError(f"Unsupported quality baseline version: {baseline['schema_version']}")
    if not all(isinstance(baseline[key], str) and baseline[key].strip()
               for key in ("source_revision", "rationale")):
        raise ValueError("Quality baseline requires its source revision and rationale")
    if not isinstance(baseline["lint_debt"], dict):
        raise ValueError("Lint debt must be an object of scoped integer counts")
    for key, count in baseline["lint_debt"].items():
        if not isinstance(key, str) or not key or type(count) is not int or count < 0:
            raise ValueError("Invalid lint-debt entry")
    validate_cycles(baseline["runtime_cycles"])
    validate_cycles(baseline["all_import_cycles"])
    validate_function_budgets(baseline["function_budgets"])
    return baseline


def validate_cycles(cycles: object) -> None:
    if not isinstance(cycles, list):
        raise ValueError("Import cycles must be an explicit list of module lists")
    for cycle in cycles:
        if not isinstance(cycle, list) or not cycle or not all(
            isinstance(module, str) and module.strip() for module in cycle
        ):
            raise ValueError("Invalid import-cycle entry")
        if len(cycle) != len(set(cycle)):
            raise ValueError("Import-cycle members must be distinct")


def validate_function_budgets(budgets: object) -> None:
    if not isinstance(budgets, dict):
        raise ValueError("Function budgets must be an object of reviewed symbol budgets")
    for symbol, limits in budgets.items():
        if not isinstance(symbol, str) or symbol.count(":") != 1 or not all(symbol.split(":")):
            raise ValueError("Function budget requires a module:function symbol")
        if not isinstance(limits, dict) or not isinstance(limits.get("rationale"), str) or not limits["rationale"].strip():
            raise ValueError(f"Function budget requires a review rationale: {symbol}")
        metrics = set(limits) - {"rationale"}
        if not metrics or not metrics <= RESPONSIBILITY_METRICS:
            raise ValueError(f"Invalid responsibility metrics: {symbol}")
        for metric in metrics:
            if type(limits[metric]) is not int or limits[metric] < 0:
                raise ValueError(f"Responsibility budget must be a nonnegative integer: {symbol} {metric}")


def pure_domain_violations(report: dict) -> list[str]:
    """Follow declared local dependencies, including deferred and type-only edges.

    Purity is a source boundary, not merely an eager-import property. A helper
    must not conceal a forbidden dependency. Use a deterministic shortest path
    for diagnostics and visit each local module once per protected root.
    """
    violations = []
    modules = report["modules"]
    for root in sorted(PURE_DOMAIN_MODULES):
        pending = deque([(root, (root,))])
        visited = {root}
        while pending:
            module, path = pending.popleft()
            details = modules.get(module)
            if details is None:
                continue
            for edge in details["declared_imports"]:
                if any(edge["target"] == prefix or edge["target"].startswith(prefix + ".")
                       for prefix in PURE_DOMAIN_POLICIES[root]):
                    route = f" via {' -> '.join(path)}" if len(path) > 1 else ""
                    violations.append(f"Forbidden pure-domain dependency: {details['path']}:{edge['line']} -> {edge['target']}{route}")
            for target in sorted({edge["target"] for edge in details["imports"]}):
                if target not in visited:
                    visited.add(target)
                    pending.append((target, (*path, target)))
    return violations


def check_report(report: dict, baseline: dict) -> list[str]:
    violations = [f"Syntax error in {error['path']}: {error['error']}"
                  for error in report["syntax_errors"]]
    for module, details in report["modules"].items():
        if not is_lower_layer(module):
            continue
        for edge in details["declared_imports"]:
            if edge["target"] == "rm_client.ui" or edge["target"].startswith("rm_client.ui."):
                violations.append(f"UI dependency from lower layer: {details['path']}:{edge['line']} -> {edge['target']}")
    violations.extend(pure_domain_violations(report))
    old_debt = baseline["lint_debt"]
    for key, count in lint_debt(report).items():
        if count > old_debt.get(key, 0):
            violations.append(f"New lint debt {key}: {old_debt.get(key, 0)} -> {count}")
    allowed_cycles = [set(cycle) for cycle in baseline["runtime_cycles"]]
    for cycle in report["runtime_cycles"]:
        if not any(set(cycle) <= allowed for allowed in allowed_cycles):
            violations.append("New module-initialization cycle: " + " -> ".join(cycle))
    allowed_full_cycles = [set(cycle) for cycle in baseline["all_import_cycles"]]
    for cycle in report["all_import_cycles"]:
        if not any(set(cycle) <= allowed for allowed in allowed_full_cycles):
            violations.append("New import cycle including deferred imports: " + " -> ".join(cycle))
    for symbol, budgets in baseline["function_budgets"].items():
        module, _, function = symbol.partition(":")
        current = report["modules"].get(module, {}).get("functions", {}).get(function)
        if current is None:
            continue
        for metric, limit in budgets.items():
            if metric == "rationale":
                continue
            value = current[metric]
            if value > limit:
                violations.append(f"Responsibility budget grew: {symbol} {metric} {limit} -> {value}")
    return sorted(set(violations))


def function_rows(report: dict) -> list[tuple[str, str, dict]]:
    return [(module["path"], name, values) for module in report["modules"].values()
            for name, values in module["functions"].items()]


def render_markdown(report: dict, violations: list[str], limit: int = 20) -> str:
    summary = report["summary"]
    lines = ["# Python architecture measurements", "",
             "Static analysis only. Metrics are review signals, not proof of single responsibility or runtime safety.",
             "No application modules were imported, no hardware was opened, and no paid CI was started.", "",
             f"- Source tree SHA-256: `{report['source_tree_sha256']}`",
             f"- Files: {summary['parsed_files']} parsed / {summary['files']} discovered",
             f"- Functions: {summary['functions']} (including {summary['historical_ui_functions']} in retained historical UI source)",
             f"- Executable AST statements: {summary['statements']}",
             f"- Decision points: {summary['decisions']}",
             f"- Local import edges: {summary['import_edges']}",
             f"- Module-initialization strongly connected components: {len(report['runtime_cycles'])}",
             f"- Including deferred imports: {len(report['all_import_cycles'])}", "",
             "## Highest executable responsibility counts", "",
             "Physical span includes multiline literals; statements exclude docstrings and nested function bodies.",
             "Decision points include Boolean short circuits and comprehensions. Counts are not a prescribed size limit.", "",
             "| Function | Line | Span | Statements | Decisions | Nesting | Direct self names | Literal state paths |",
             "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    rows = sorted(function_rows(report), key=lambda row: (-row[2]["statements"], row[0], row[1]))[:limit]
    for path, name, values in rows:
        state = len(set(values["state_reads"]) | set(values["state_writes"]))
        lines.append(f"| `{path}:{name}` | {values['line']} | {values['source_span']} | {values['statements']} | "
                     f"{values['decisions']} | {values['max_control_depth']} | {len(values['self_members'])} | {state} |")
    lines += ["", "## Local module coupling", "", "| Module | Fan in | Fan out |", "| --- | ---: | ---: |"]
    for name, module in sorted(report["modules"].items(), key=lambda pair: (-pair[1]["fan_out"], pair[0]))[:limit]:
        lines.append(f"| `{name}` | {module['fan_in']} | {module['fan_out']} |")
    lines += ["", "## Import cycles", ""]
    for label, key in (("Initialization", "runtime_cycles"), ("Including deferred imports", "all_import_cycles")):
        lines.append(f"{label}:")
        lines += ["- " + ", ".join(f"`{name}`" for name in cycle) for cycle in report[key]] or ["- None found"]
        lines.append("")
    counts = Counter(finding["rule"] for module in report["modules"].values() for finding in module["findings"])
    lines += ["## Existing lint debt", "", *[f"- {rule}: {count}" for rule, count in sorted(counts.items())],
              "", "## Gate result", "", *([f"- {violation}" for violation in violations] or ["No ratchet or dependency-boundary violations."]),
              "", "## Limits", "",
              "Imports are statically resolved within the measured source tree. Dynamic imports, reflective calls,",
              "alias-based state access, implicit package-initializer execution, and effects hidden inside callees are not inferred. TYPE_CHECKING imports",
              "are reported but excluded from runtime cycles; function-local imports are only in the deferred graph.",
              "The protected pure-domain roots reject forbidden declared dependencies through resolved local helpers, including deferred and type-only edges. This is not a general proof of runtime purity.",
              "State access counts cover literal `self.state.field` and `state.field` paths; they do not establish field ownership.",
              "Direct self names include method calls and collaborators, not just mutable fields. No metric here qualifies a robot or GPU.",
              "Historical UI implementation remains included in every total. Its source classification only separates legacy module-name evidence from current-runtime coverage; moving it is not a complexity reduction.", ""]
    return "\n".join(lines)
