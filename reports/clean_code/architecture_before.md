# Python architecture measurements

Static analysis only. Metrics are review signals, not proof of single responsibility or runtime safety.
No application modules were imported, no hardware was opened, and no paid CI was started.

- Source tree SHA-256: `cd432aed2f9188765812905cab9e76a61aac6c36980d6f8bec3100d91138aca5`
- Files: 141 parsed / 141 discovered
- Functions: 2619 (including 50 in retained historical UI source)
- Executable AST statements: 34124
- Decision points: 11995
- Local import edges: 423
- Module-initialization strongly connected components: 0
- Including deferred imports: 3

## Highest executable responsibility counts

Physical span includes multiline literals; statements exclude docstrings and nested function bodies.
Decision points include Boolean short circuits and comprehensions. Counts are not a prescribed size limit.

| Function | Line | Span | Statements | Decisions | Nesting | Direct self names | Literal state paths |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `rm_client/ui/polar_app.py:PolarRMWindow.__init__` | 379 | 387 | 289 | 8 | 1 | 270 | 3 |
| `rm_client/ui/minecraft_mod_items.py:crafted_item` | 54 | 196 | 284 | 62 | 3 | 0 | 0 |
| `rm_client/ui/polar/mixins/economy_mixin.py:EconomyMixin.build_gpu_economy_workspace` | 941 | 599 | 240 | 90 | 3 | 25 | 0 |
| `rm_client/ui/polar_app.py:PolarRMWindow._paint_gpu_frame_cached` | 1632 | 424 | 226 | 92 | 5 | 61 | 0 |
| `rm_client/ui/polar/mixins/economy_mixin.py:EconomyMixin.draw_economy_overlay_compat` | 480 | 460 | 221 | 73 | 3 | 26 | 0 |
| `rm_client/backend/manager.py:BackendManager._tick_polar_link_ready` | 720 | 505 | 191 | 134 | 4 | 16 | 0 |
| `rm_client/ui/polar/mixins/input_dispatch_mixin.py:InputDispatchMixin.keyPressEvent` | 895 | 237 | 183 | 109 | 4 | 56 | 1 |
| `rm_client/backend/referee/rm2026/state_adapter.py:Rm2026StateAdapter._apply_one` | 160 | 231 | 176 | 82 | 4 | 7 | 79 |
| `rm_client/cs2_crosshair.py:_create_editor.Editor.__init__` | 701 | 219 | 173 | 25 | 5 | 37 | 0 |
| `rm_client/ui/polar/mixins/config_actions_mixin.py:ConfigActionsMixin.handle_qualification_action` | 462 | 291 | 142 | 74 | 3 | 19 | 7 |
| `rm_client/ui/polar/mixins/config_actions_mixin.py:ConfigActionsMixin.handle_config_action` | 227 | 234 | 132 | 64 | 4 | 27 | 9 |
| `rm_client/ui/polar/renderers/module_list.py:ModuleListMixin.draw_module_list_debug_white` | 473 | 235 | 132 | 45 | 3 | 19 | 0 |
| `rm_client/ui/cs2_buy_menu.py:build_grid` | 75 | 154 | 116 | 50 | 3 | 0 | 0 |
| `rm_client/config_store.py:apply_registry_config` | 231 | 154 | 114 | 61 | 4 | 0 | 0 |
| `rm_client/ui/polar_app.py:PolarRMWindow.on_tick` | 767 | 220 | 111 | 55 | 2 | 81 | 7 |
| `rm_client/ui/economy_glass.py:draw_economy_glass` | 46 | 177 | 109 | 7 | 2 | 0 | 0 |
| `rm_client/ui/minecraft_hud.py:MinecraftCombatHudRenderer.draw` | 451 | 232 | 105 | 38 | 4 | 25 | 0 |
| `rm_client/ui/jello_clickgui.py:JelloClickGuiRenderer._build_settings_modal` | 1137 | 239 | 104 | 25 | 4 | 11 | 15 |
| `rm_client/ui/jello_clickgui.py:JelloClickGuiRenderer._build_setting` | 1411 | 262 | 103 | 29 | 4 | 9 | 8 |
| `rm_client/kill_fx.py:ValorantRenderer.draw` | 907 | 109 | 101 | 52 | 1 | 6 | 0 |

## Local module coupling

| Module | Fan in | Fan out |
| --- | ---: | ---: |
| `rm_client.ui.polar_app` | 2 | 73 |
| `rm_client.backend.manager` | 0 | 25 |
| `rm_client.ui.polar.mixins.economy_mixin` | 1 | 15 |
| `rm_client.ui.polar.mixins.video_backend_mixin` | 1 | 14 |
| `rm_client.ui.polar.mixins.config_actions_mixin` | 1 | 12 |
| `rm_client.ui.cs2_buy_menu` | 3 | 10 |
| `rm_client.ui.polar.mixins.clickgui_builders_mixin` | 1 | 10 |
| `rm_client.ui.polar.renderers.combat_target_hud` | 2 | 10 |
| `rm_client.ui.polar.renderers.startup_scene` | 1 | 10 |
| `rm_client.ui.cs2_settings_center` | 3 | 9 |
| `rm_client.ui.polar.renderers.module_list` | 1 | 9 |
| `rm_client.model` | 19 | 8 |
| `rm_client.ui.polar.mixins.input_dispatch_mixin` | 1 | 8 |
| `rm_client.ui.polar.mixins.persistence_mixin` | 2 | 8 |
| `rm_client.ui.polar.renderers.esp_wireframe` | 1 | 8 |
| `rm_client.ui.polar.renderers.tactical_map` | 1 | 8 |
| `rm_client.ui.top_match_bar_controller` | 1 | 8 |
| `rm_client.backend.referee.registry` | 6 | 7 |
| `rm_client.ui.polar.renderers.dynamic_island` | 1 | 7 |
| `rm_client.ui.student_panels` | 1 | 7 |

## Import cycles

Initialization:
- None found

Including deferred imports:
- `rm_client.backend.referee.registry`, `rm_client.backend.referee.rm2026.economy_rules`, `rm_client.backend.referee.rm2026.state_adapter`, `rm_client.backend.referee.student_legacy.state_adapter`, `rm_client.economy`, `rm_client.model`, `rm_client.scenarios`, `rm_client.student_ui`
- `rm_client.ui.cs2_settings_center`, `rm_client.ui.jello_keybinds`
- `rm_client.ui.polar.app_entry`, `rm_client.ui.polar_app`

## Existing lint debt

- inline-suite: 149
- multiple-statements: 405
- wildcard-import: 4

## Gate result

No ratchet or dependency-boundary violations.

## Limits

Imports are statically resolved within the measured source tree. Dynamic imports, reflective calls,
alias-based state access, and effects hidden inside callees are not inferred. TYPE_CHECKING imports
are reported but excluded from runtime cycles; function-local imports are only in the deferred graph.
State access counts cover literal `self.state.field` and `state.field` paths; they do not establish field ownership.
Direct self names include method calls and collaborators, not just mutable fields. No metric here qualifies a robot or GPU.
Historical UI implementation remains included in every total. Its source classification only separates legacy module-name evidence from current-runtime coverage; moving it is not a complexity reduction.
