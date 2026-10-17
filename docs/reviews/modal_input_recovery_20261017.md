# Modal input ownership recovery checkpoint

Base: public PR 19 source `26cde0417697d23e3cb7e1ee6dd30ec8acd9937d`.

## Fault and correction
A modal can open and close between UI ticks. Sampling input ownership on the next tick misses the interval; a consumed W key-up leaves old motion held. The coordinator now invokes input retirement immediately after assigning modal/chat/radial ownership. Escape follows the same path. A reentrancy guard prevents output callbacks from injecting fresh motion during retirement. Independent egress neutralization is attempted even after an exception. Failed cleanup blocks keyboard/mouse admission and periodic control publication until explicit successful lock cleanup. It does not automatically arm control.

The normal sampled ownership check remains a fallback. Existing neutral transmission/backoff semantics are preserved. No shaders or UI visuals are changed.

## Executed scoped checks
- The new four-method actual-window/inert-sink regression against the unchanged public base: 8 failures, exit 1.
- The same four methods against this patch: pass. Seven overlay entry paths, held buttons/relative input, transient chat, genuine fresh press, failure isolation and output reentry are covered.
- Focused regression: ten test files / 114 unittest methods passed. Files: test_modal_input_epoch, test_mouse_button_admission, test_window_api_contract, test_window_tick_contract, test_window_focus_loss, test_escape_menu, test_window_configuration_quiescence, test_keyboard_routing_characterization, test_window_normal_frame, test_window_economy_commands.
- First focused candidate failed two existing fixtures that only observed release_all. They now observe reset plus each independent neutral egress; ordering and exact counts remain asserted. No time limits or assertions were removed to obtain a pass.
- Dependencies installed from the repository's exact hash-locked Linux CPython 3.12 verification requirements.

## Limits
This is root-authored and root-reviewed; no independent reviewer has approved this recovery patch yet. Full offline regression is pending. No Windows, GPU, physical audio, serial hardware or real robot qualification is claimed. Previous scores on other source commits do not apply. New raw execution logs remain local pending the existing publication permission request.

## Follow-up found during complete regression
The full run of source 0b92c3f exposed the native-video smoke fixture replacing the backend without its MQTT configuration port. Its fixture now preserves the original MQTT port; video authority assertions are unchanged. The exact failing native-video smoke function passes after the fixture correction. The full original run continues on its frozen source and will retain its failure.

Coordinator notification is also idempotent for repeated requests by the same owner, preventing repeated neutral egress when ownership has not changed. A new direct test covers repeated modal/chat/radial requests and a genuine close/reopen. Five modal regression methods pass on this follow-up. This follow-up has no completed full-profile score yet.

## Completed full offline verification

Exact tested source: `856406ca21f36592c53b66bdd0acd42ef327b4dc`, tree `602f475aecf5d6f29e3daa9f8c64bb4c5d92c7af`. Public equivalent: `050b5302b37deaf12fc938bbcedf12ffef15f473`.

Result: **324 PASS / 12 SKIP / 4 INCOMPLETE / 0 FAIL**. The aggregate remains **NO-GO**. All 673 declared source inputs retained identical start/end SHA256 `249c9c65784f59aef7c808bc5dfb197ed90c8500e1f9e7d68d48c3024bddc89a`.

Incomplete checks: team CRC reference unavailable (polar_link_test); selected firmware and dump.cs unavailable (test_dt7_link); selected firmware unavailable (test_dt7_ui_selftest); legacy patch test requires installation outside read-only validation (test_patch_logic). No whole-project, physical control, Windows/GPU, or independent-review qualification is implied.

The preceding source 0b92c3f result remains **323 / 12 / 4 / 1**; its failed native-video fixture result is retained separately. This report records the result without publishing the permission-pending raw logs.
