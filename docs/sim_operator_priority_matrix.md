# 22-priority operator integration matrix

This checklist covers the 22 immediate operator paths, not completion of all 73
registry modules or their 417 settings. Repository defaults are not proof of the
user's current active configuration. The original Jello/MC+CS presentation is
retained; normal device mode and SIM remain separate.

| # | Priority path | Current status | Verified scope / remaining boundary |
|---|---|---|---|
| 1 | WASD movement | Implemented, bounded | Original keys to chassis-body motion; no field/gimbal following |
| 2 | Mouse gimbal | Implemented, bounded | Cumulative angle, generation/neutral ACK, measured settling; legacy-rate endpoints labelled RTF-dependent |
| 3 | Left-button manual fire | Implemented, bounded | Virtual manual fire/ammo/heat path; no automatic fire grant |
| 4 | SpinMode | Partial | Original toggle, Auto/CW/CCW/speed and measured response; no power guard or world-stabilized gimbal |
| 5 | AutoSpinOnHit | Partial | Actual projectile-credit observation, original policy, one-use same-lease transition, physical simulated hit/yaw check; settings subset only |
| 6 | ArmorAutoAim | Partial | Qualified selected source can take gimbal authority; supported robot and stationary-only restrictions apply |
| 7 | Angular Fov | Partial | Real source admission in bounded stationary scene; general moving-source robustness unqualified |
| 8 | Circle Size / Draw Fov | Implemented, bounded | Original circle, actual letterboxed camera geometry, source ellipse intersection; not a target-lock indicator |
| 9 | Right-button aim hold/release | Implemented, bounded | Original Qt hold to request source, release withdraws authority; new request cannot reuse old confirmation |
| 10 | FrictionWheel | Unsupported in SIM | No observed/controlled RPM model; SIM N/A |
| 11 | Burst / firing modes | Unsupported in SIM | Manual firing is not proof that original burst/rate/safety settings are consumed |
| 12 | ChassisAssist | Partial | Body-relative Free only; Gimbal/Field following and power/buffer protection unavailable |
| 13 | Key bindings | Partial | Original movement binding path retained; all module/global key conflicts not desktop-qualified |
| 14 | Robot/team switching | Unsupported in SIM UI | Current session displayed authoritatively; selection belongs to bridge startup, not unrelated local profiles |
| 15 | Referee/HUD | Partial | Actual own HP/ammo/heat, phase/time/VP and roster subset; no invented official packet or unknown power values |
| 16 | Hit/kill events and effects | Partial | Actual own projectile damage is consumed for hit-spin; full injury, kill/effects and settlement mapping incomplete |
| 17 | Minimap | Partial | Labelled simulation XY for display; not perception input, radar observation or map command support |
| 18 | ESC / ClickGUI | Implemented, bounded | Original menu/input gates clear virtual control; full desktop interaction matrix pending |
| 19 | F12 / F2 | Implemented, bounded | Virtual stop and explicit rearm; F2 is the documented SIM rearm action |
| 20 | Focus / timeout / disconnect | Implemented, bounded | Independent wall-clock leases, stale-frame gates, no held-input replay |
| 21 | Power / buffer / capacitor | Unsupported in SIM | No validated model/observations; controls and indicators stay unavailable |
| 22 | Fonts / final visual rendering | Partial, CPU glyph fix qualified | Native CJK strokes preserved through 2x HUD cache; original-window CPU A/B and pixel tests pass; desktop/OpenGL and all panels remain unqualified |

The bounded original-Qt source/FOV workflow is described in
[validation scope](sim_operator_validation_scope.md). The physical simulated
hit-spin check and its reproducible entry are described in
[hit-spin physics validation](sim_hit_physics_validation.md).

The cache/font fix and its memory/performance boundary are documented in
[font raster validation](minecraft_font_raster.md). Remaining unsupported control
paths and broader source-aim qualification still need separate increments; a
readable HUD does not complete all 73 modules or the desktop rendering matrix.
