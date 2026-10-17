"""Narrow static evidence for dynamically named configuration actions.

A command declaration is not a consumer. This recognizes the current public
resolver -> adapter -> terminal-handler route, then asks the Qt-free resolver
whether an exact option is registered. Evidence remains DYNAMIC: executing the
handler and its effects is the responsibility of configuration command tests.
"""
from __future__ import annotations

import ast
from pathlib import Path

from rm_client.configuration.commands import resolve_config_command


DISPATCH_SOURCE = Path("rm_client/ui/polar/mixins/config_actions_mixin.py")
# These are actual consumer boundaries, not declarations or UI labels.
DYNAMIC_ACTION_HANDLERS = {
    "SOUND_IMPORT": "import_custom_ui_sound",
    "SOUND_PREVIEW": "_preview_ui_sound",
}


def _contains_call(node: ast.AST, expression: str) -> bool:
    expected = ast.dump(ast.parse(expression, mode="eval").body)
    return any(isinstance(child, ast.Call) and ast.dump(child) == expected
               for child in ast.walk(node))


def dynamic_action_routes(source: str) -> dict[str, int]:
    """Return recognized kind -> consumer line, failing closed on disconnection."""
    tree = ast.parse(source)
    imports = {alias.name for node in tree.body if isinstance(node, ast.ImportFrom)
               and node.module == "configuration.commands" and node.level == 4
               for alias in node.names if alias.asname in (None, alias.name)}
    if not {"resolve_config_command", "ConfigCommandKind"} <= imports:
        return {}
    owner = next((node for node in tree.body if isinstance(node, ast.ClassDef)
                  and node.name == "ConfigActionsMixin"), None)
    if owner is None:
        return {}
    methods = {node.name: node for node in owner.body
               if isinstance(node, ast.FunctionDef)}
    entry = methods.get("handle_config_action")
    dispatch = methods.get("_execute_config_command")
    if entry is None or dispatch is None:
        return {}
    resolution = ast.dump(ast.parse("command = resolve_config_command(action_name)").body[0])
    if not any(isinstance(node, ast.Assign) and ast.dump(node) == resolution
               for node in ast.walk(entry)):
        return {}
    if not _contains_call(entry, "self._execute_config_command(command)"):
        return {}
    routes = {}
    for kind, handler in DYNAMIC_ACTION_HANDLERS.items():
        condition = ast.dump(ast.parse(
            f"command.kind is ConfigCommandKind.{kind}", mode="eval").body)
        for node in ast.walk(dispatch):
            if not isinstance(node, ast.If) or ast.dump(node.test) != condition:
                continue
            for statement in node.body:
                if _contains_call(statement, f"self.{handler}(command.sound_slot)"):
                    routes[kind] = statement.lineno
    return routes


def dynamic_action_evidence(option: str, routes: dict[str, int]) -> list[str]:
    """An exact registered command plus a consumer route is dynamic evidence."""
    command = resolve_config_command(option)
    if command is None or command.kind.name not in routes:
        return []
    return [f"{DISPATCH_SOURCE.as_posix()}:{routes[command.kind.name]}  "
            f"resolve_config_command({option!r}) -> {command.kind.name} "
            f"-> {DYNAMIC_ACTION_HANDLERS[command.kind.name]}({command.sound_slot!r})"]
