# Configuration participant matrix and rollback resolution

Source baseline: `666c040`. This bounded review distinguishes existing contracts
from checks executed in this stage. It does not claim all live effects are one
atomic transaction or that native device restoration was exercised.

## Participant ownership and ordering

| Participant | Production owner / ordered effect | Recovery contract and evidence | Remaining boundary |
| --- | --- | --- | --- |
| Registry values/options/module binds | `configuration.registry.RegistrySnapshot`; settings adapter writes all prepared values before callbacks, then toggles/binds | Snapshot restore before runtime and again afterward; no toggle listeners during restoration. `test_configuration_boundaries`, `test_configuration_transactions` | A snapshot is mutable-object restoration, not process/crash isolation. |
| Control binds and output authorization | `ui.settings_application.apply_registry_draft`; lock, release, clear pending before applying canonical bind map | `PersistenceMixin.fail_closed_configuration` restores binds/view state, triggers estop and locks in finally. Window tests verify cleared held input and no arm | Physical neutral delivery remains hardware evidence; a local clear is not firmware acknowledgment. |
| Accepted profile and pending save identity | `configuration.profile_saves.ProfileSaveState` plus persistence adapter | Profile acceptance follows matching lifecycle completion. Delayed saves bind to accepted identity/generation. Existing `test_profile_save_contract`, `test_profile_save_identity`, `test_profile_save_state` | These suites were inventoried, not rerun in this stage. File success and UI selector success are distinct publication phases. |
| Audio preference bytes | `audio_preferences.AudioPreferenceChange` / `configuration.files.FileSnapshot` | Captures original bytes; restore admission begins before write attempt; retry remains owned until file and pools recover. Reverse transaction participant rollback | Single admitted writer and ordinary exceptions only; no multiwriter arbitration or crash-atomic bundle. |
| Audio cache and pools | `_refresh_audio_pools` clears both caches then attempts all pools; preference participant covers file/cache/pools | `test_audio_configuration_transactions` exercises write/pool failures and registry recovery, including partial device setter failure | Fake audio/device calls, not physical playback/reconnect qualification. |
| UI cache/projection and notification | Settings adapter invalidates GUI/transient cache then forces frame before auxiliary participant commit; persistence `_restore_configuration_view` restores non-module view data | Window transaction tests retain failed draft and restore settings/options/view without replaying enable hooks | Cache recomputation/notification is not rollback of rendered history; autosave scheduling is post-transaction. |
| Scenario/runtime generation | `PendingConfigurationChange`, `_request_registry_runtime`, single lifecycle completion adapter | Failed activation uses stopped restoration (`start=False`), retains pending status/errors, then exact snapshot and fail-closed cleanup | Pending is incomplete recovery, not success. External/native-owner completion has separate lifecycle evidence. |
| Rollback callback resolution | `configuration.transactions.rollback_configuration_transaction` | This stage fixes eager callback resolution escaping all subsequent restoration and fail-closed work | Adversarial protocol-adapter failure, not an observed descriptor on the current concrete audio participant. |

## Reproduced defect and narrow change

The previous rollback tuple evaluated every `participant.rollback` attribute
before entering its per-operation try block. A property-backed participant whose
rollback lookup raised after a partial commit prevented registry restoration,
other participant restoration, runtime recovery and fail-closed cleanup. It also
lost the structured `ConfigurationApplyError` describing the original cause.

Red commit `4d04f36` executes this scenario against an inert registry and two
participants. It failed with an unhandled `OSError` from callback resolution.
The original raw run remains under ignored `tmp_quality/rollback-resolution-red.log`.
This is a reproduced violation of the participant protocol's recovery guarantee;
no claim is made that `AudioPreferenceChange` currently has a failing property.

The change delays each participant's callback lookup until its existing guarded
rollback operation. Registry restoration, reverse participant ordering, stopped
runtime recovery, second exact restoration and final fail-closed invocation
remain unchanged. No fallback suppresses errors: lookup errors are retained in
`restore_errors`, runtime and safety errors remain separately reportable, and
`restored` remains false. Successful transactions never resolve rollback callbacks.

## Executed verification

Linux x86_64, pinned CPython 3.12.14 environment. `PYTHON` below means the prepared
hash-pinned verification interpreter. Offscreen runs used `CI=true`,
`POLAR_AUTOSTART=0`, disabled local capture/media preload and owned XDG cache,
config/data directories under ignored `tmp_quality`.

```sh
"$PYTHON" tests/test_configuration_rollback_resolution.py
"$PYTHON" tests/test_configuration_boundaries.py
QT_QPA_PLATFORM=offscreen "$PYTHON" tests/test_audio_configuration_transactions.py
QT_QPA_PLATFORM=offscreen "$PYTHON" tests/test_configuration_transactions.py
"$PYTHON" tests/test_verification_dependency_lock.py
"$PYTHON" -m pip check
"$PYTHON" tools/quality_gate.py
git diff --check
```

Results: 3 + 18 + 9 + 10 + 4 = **44 tests passed**, no skips. `pip check` found
no broken requirements; static gate passed (219 Python modules, 3,428 functions,
zero initialization cycles). Headless audio warnings are not device evidence.
No dependencies were installed or fetched. Lock consistency/pip metadata checks
do not independently re-hash already-installed package files. No aggregate
verifier was run; the integrating owner controls its lock and final source run.

## Follow-on decisions

Retain the current explicit owner boundaries while reviewing actual failures;
do not replace them with one global rollback abstraction. Pending runtime
restoration, post-commit UI notification/autosave failure, individual registry
setter failure, and multiple admitted preference writers have different
semantics. Any next change needs a concrete production-path reproduction and
its own negative control. Native Windows packaging, audio hardware and robot
qualification remain separate blockers, not completed by these local results.
