# Python architecture measurements

Static analysis only. Metrics are review signals, not proof of single responsibility or runtime safety.
No application modules were imported, no hardware was opened, and no paid CI was started.

- Source tree SHA-256: `70538a0cdb59627162e01682bd703dea0b1fbb00193c45bff82e201e23be16fa`
- Files: 213 parsed / 213 discovered
- Functions: 3396 (including 50 in retained historical UI source)
- Executable AST statements: 37179
- Decision points: 13082
- Local import edges: 605
- Module-initialization strongly connected components: 0
- Including deferred imports: 0

## Highest executable responsibility counts

Physical span includes multiline literals; statements exclude docstrings and nested function bodies.
Decision points include Boolean short circuits and comprehensions. Counts are not a prescribed size limit.

| Function | Line | Span | Statements | Decisions | Nesting | Direct self names | Literal state paths |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `rm_client/ui/minecraft_mod_items.py:crafted_item` | 54 | 196 | 284 | 62 | 3 | 0 | 0 |
| `rm_client/ui/polar_app.py:PolarRMWindow.__init__` | 466 | 363 | 247 | 2 | 1 | 229 | 3 |
| `rm_client/backend/referee/rm2026/state_adapter.py:Rm2026StateAdapter._apply_one` | 163 | 219 | 174 | 72 | 3 | 7 | 81 |
| `rm_client/cs2_crosshair.py:_create_editor.Editor.__init__` | 328 | 219 | 173 | 25 | 5 | 37 | 0 |
| `rm_client/ui/polar/mixins/input_dispatch_mixin.py:InputDispatchMixin.keyPressEvent` | 927 | 226 | 171 | 100 | 4 | 55 | 1 |
| `rm_client/ui/polar/renderers/module_list.py:ModuleListMixin.draw_module_list_debug_white` | 473 | 235 | 132 | 45 | 3 | 19 | 0 |
| `rm_client/ui/polar/mixins/economy_mixin.py:EconomyMixin.build_gpu_economy_workspace` | 716 | 332 | 131 | 45 | 3 | 25 | 0 |
| `rm_client/ui/polar/mixins/economy_mixin.py:EconomyMixin.draw_economy_overlay_compat` | 482 | 233 | 123 | 33 | 3 | 25 | 0 |
| `rm_client/ui/cs2_buy_menu.py:build_grid` | 75 | 154 | 116 | 50 | 3 | 0 | 0 |
| `rm_client/ui/economy_glass.py:draw_economy_glass` | 46 | 177 | 109 | 7 | 2 | 0 | 0 |
| `rm_client/ui/minecraft_hud.py:MinecraftCombatHudRenderer.draw` | 451 | 232 | 105 | 38 | 4 | 25 | 0 |
| `rm_client/ui/jello_clickgui.py:JelloClickGuiRenderer._build_settings_modal` | 1137 | 239 | 104 | 25 | 4 | 11 | 15 |
| `rm_client/ui/jello_clickgui.py:JelloClickGuiRenderer._build_setting` | 1411 | 262 | 103 | 29 | 4 | 9 | 8 |
| `rm_client/ui/polar/mixins/input_dispatch_mixin.py:InputDispatchMixin.wheelEvent` | 815 | 111 | 102 | 30 | 2 | 36 | 0 |
| `rm_client/kill_fx.py:ValorantRenderer.draw` | 907 | 109 | 101 | 52 | 1 | 6 | 0 |
| `rm_client/combat_events.py:VisionKillDetector._finalize_tracks` | 594 | 129 | 97 | 41 | 4 | 13 | 0 |
| `rm_client/ui/polar_app.py:PolarRMWindow._paint_gpu_frame_cached` | 1658 | 238 | 97 | 46 | 3 | 45 | 0 |
| `rm_client/ui/cs2_video_settings.py:open_video_settings` | 143 | 97 | 93 | 13 | 1 | 0 | 0 |
| `rm_client/ui/cs2_settings_center.py:RegistrySettingsPage._build_rows` | 143 | 75 | 91 | 15 | 4 | 9 | 0 |
| `rm_client/combat_events.py:VisionKillDetector.process` | 724 | 144 | 90 | 40 | 3 | 23 | 0 |

## Local module coupling

| Module | Fan in | Fan out |
| --- | ---: | ---: |
| `rm_client.ui.polar_app` | 2 | 94 |
| `rm_client.backend.manager` | 0 | 36 |
| `rm_client.ui.polar.window_assembly` | 1 | 24 |
| `rm_client.ui.polar.mixins.economy_mixin` | 1 | 21 |
| `rm_client.ui.polar.mixins.config_actions_mixin` | 1 | 17 |
| `rm_client.ui.cs2_settings_center` | 2 | 15 |
| `rm_client.ui.polar.mixins.video_backend_mixin` | 1 | 15 |
| `rm_client.ui.polar.mixins.input_dispatch_mixin` | 1 | 12 |
| `rm_client.ui.polar.mixins.persistence_mixin` | 2 | 12 |
| `rm_client.ui.polar.renderers.startup_scene` | 1 | 11 |
| `rm_client.ui.polar.renderers.tactical_map` | 1 | 11 |
| `rm_client.model` | 20 | 10 |
| `rm_client.ui.cs2_buy_menu` | 3 | 10 |
| `rm_client.ui.polar.mixins.clickgui_builders_mixin` | 1 | 10 |
| `rm_client.ui.polar.renderers.combat_target_hud` | 2 | 10 |
| `rm_client.ui.polar.renderers.module_list` | 1 | 9 |
| `rm_client.backend.referee.registry` | 4 | 8 |
| `rm_client.ui.polar.renderers.esp_wireframe` | 1 | 8 |
| `rm_client.ui.top_match_bar_controller` | 1 | 8 |
| `rm_client.ui.economy_content` | 1 | 7 |

## Import cycles

Initialization:
- None found

Including deferred imports:
- None found

## Existing lint debt

- inline-suite: 149
- multiple-statements: 400
- wildcard-import: 4

## Gate result

No ratchet or dependency-boundary violations.

## Limits

Imports are statically resolved within the measured source tree. Dynamic imports, reflective calls,
alias-based state access, implicit package-initializer execution, and effects hidden inside callees are not inferred. TYPE_CHECKING imports
are reported but excluded from runtime cycles; function-local imports are only in the deferred graph.
The protected pure-domain roots reject forbidden declared dependencies through resolved local helpers, including deferred and type-only edges. This is not a general proof of runtime purity.
State access counts cover literal `self.state.field` and `state.field` paths; they do not establish field ownership.
Direct self names include method calls and collaborators, not just mutable fields. No metric here qualifies a robot or GPU.
Historical UI implementation remains included in every total. Its source classification only separates legacy module-name evidence from current-runtime coverage; moving it is not a complexity reduction.
