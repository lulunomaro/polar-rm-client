# External acceptance inputs — 2026-10-17

## Recorded result stays NO-GO

The latest completed aggregate is **349 PASS / 12 SKIP / 4 INCOMPLETE /
0 FAIL, NO-GO** (`passed=false`), verified directly in
`../client-reference-validation-full/results.json`, run
`c31f695a08f140c19e8f8e029b356ae3`. Its clean starting source is local
`99167e88675b66a509f9a8266c71175747452a9e`, tree
`80e30d54bfee9dcb996726f71132510095126cc3`; the published counterpart is
`734ff5a8356e9571bd8874883ccc02aa3767c9a0`.
All 698 declared verification inputs have matching start/end SHA-256
`921161f1d2ca8a089fedb769cf236407919a33eecc4aaf858a7319efdb5c096e`
(source stability PASS). This is endpoint comparison, not coverage of assets,
external fixtures, installed packages or transient edits restored between
captures. No aggregate remains running at this update.

The historical presentation-prefix aggregate was **348 PASS / 12 SKIP /
4 INCOMPLETE / 0 FAIL**, `passed=false`, at public
`c4ceb891da2155eb3038d2960f3493e1eb7b9fb4` / local
`4dac2a53357c9a514bb958dc7f7a1a13162df57e`, tree
`067b71d9f5f0fdb12ae02aad81a110e9264dc7c8`.
See [the recorded checkpoint](window_early_resource_audit_20261017.md).
This handoff does not rerun or reclassify that result.

## Inputs needed

1. **Team Python CRC reference** for `tests/polar_link_test.py`:
   `Serial_Port_Assistant/lib/CRC8_CRC16.py` from commit
   `af97031fb8dde62e15f2f33e686b0d35234a13a4`, SHA-256
   `f46cce6382bbbae2056b81538c1de5c06a93c2199b4d07784d9bc551509ed2f5`.
   Select the verified local file with `POLAR_TEAM_CRC_REFERENCE`.
   The loader executes this Python file; caller-supplied trusted provenance and
   authorized execution are required. A missing explicit path is an error.
2. **Actual StandardRobotpp source** for
   `tools/dt7_link/test_dt7_link.py` and
   `tools/dt7_link/test_dt7_ui_selftest.py`: documented baseline commit
   `2722e0d2daba6113c86544d0c6d7918672669601`.
   Set `POLAR_FIRMWARE_ROOT` for standalone checks; the aggregate selects it via
   `--firmware /absolute/path` and forces read-only mode. CRC comparison needs
   `components/support/CRC8_CRC16.c` and its real build dependencies. The UI
   self-test scans actual `application/**/*.c` for `KEY_X` consumers; a hand-made
   empty source tree is not evidence that the real firmware has none.
3. **Authentic vendor `dump.cs`** for the DT7 type-layout anchor, with vendor
   version, acquisition provenance and SHA-256 recorded before use. No accepted
   vendor hash is available here. `POLAR_VENDOR_DUMP=/absolute/path/dump.cs`
   now selects the caller-provided file. An explicitly selected missing path
   never falls back to another file; without the override, the Windows default
   `D:\rmbuild\dump12\dump.cs` remains unchanged. This portability support
   does not provide or authenticate the vendor input.
4. **Separate disposable baseline firmware checkout** for
   `tools/firmware/test_patch_logic.py`, plus an already approved Python build
   environment containing `ziglang==0.13.0` (`requirements-build.txt`) and Git.
   This is supplemental host-C qualification, not a way to bypass the
   aggregate's read-only contract.

The first two upstream pins are recorded in
[serial protocol hardening](../serial_protocol_hardening.md), with historical
CRC provenance in
`docs/evidence/clean-code-20261016/secondpass-wave2/reference-manifest.json`.
Historical hashes are selection criteria, not proof those inputs are currently
present. Static fake fixtures can check parser/error behavior but cannot replace
third-party CRC, firmware or vendor-layout evidence.

## Input-validation checkpoint

The vendor override and empty-firmware-source validation were independently
accepted at public `734ff5a8356e9571bd8874883ccc02aa3767c9a0` / local
`99167e88675b66a509f9a8266c71175747452a9e`, tree
`80e30d54bfee9dcb996726f71132510095126cc3`: five focused tests PASS;
baseline negative control five FAIL / zero ERROR. A missing or empty
`application` C-source inventory remains INCOMPLETE, rather than vacuously
passing the `KEY_X` consumer check. These fixtures validate input handling,
not the absent third-party evidence.

The completed 349/12/4/0 run above retains all four incomplete entries.
Partial standalone script results (116 and 35 passing assertions) do not close
the missing prerequisites or establish qualification.

## Separate legacy patch qualification, when authorized

Use a fresh disposable clone of caller-provided trusted local firmware. Do not
use an operational, prepared, shared or dirty firmware tree. Proposed commands
below have **not** been executed for this handoff; replace placeholders with
verified absolute paths and an existing approved interpreter:

```sh
git clone --no-hardlinks /trusted/StandardRobotpp /disposable/firmware-baseline
git -C /disposable/firmware-baseline checkout --detach \
  2722e0d2daba6113c86544d0c6d7918672669601
env -u POLAR_FIRMWARE_READ_ONLY \
  POLAR_FIRMWARE_ROOT=/disposable/firmware-baseline \
  /approved/python tools/firmware/test_patch_logic.py \
  > /separate-evidence/patch-logic.log 2>&1
status=$?
printf 'patch_logic_exit=%s\n' "$status"
```

Run from the pinned client checkout. Record interpreter/compiler versions,
client and firmware identities, patch SHA-256, exit code, completion marker,
assertions, and before/after firmware file hashes. Require exit 0 and
`ALL FIRMWARE PATCH LOGIC TESTS PASSED`, with no failed assertions. Missing
prerequisites or partial output remain incomplete evidence.

The script checks out two source files and applies the patch, then restores
byte snapshots (the helper also snapshots the CDC ingress file). Restoration
is not crash-safe or concurrency-safe. It compiles and executes extracted C
with handwritten stubs, not the entire ARM/Keil firmware. It tests the legacy
OR merge; the prepared host bridge uses newer key-ledger behavior. Actual
prepared-target checks include `test_patch_real_headers.py` and
`host_bridge/test_rc_key_ledger.py`, with their own prerequisites and evidence.
No flashing, board execution, native UI/audio qualification or physical-control
acceptance follows from a host-C pass.

## Keep the four incomplete entries visible

- `tests/polar_link_test`: missing team CRC comparison
- `tools/dt7_link/test_dt7_link`: missing firmware CRC and vendor-layout anchors
- `tools/dt7_link/test_dt7_ui_selftest`: missing real firmware cross-check
- `tools/firmware/test_patch_logic`: installation excluded by read-only policy

`tools/verify_all.py` always sets `POLAR_FIRMWARE_READ_ONLY=1`; the legacy patch
script immediately prints `[INCOMPLETE]` and exits 77 under that policy, even
with a firmware tree. The classifier preserves this as INCOMPLETE and requires
all selected checks to PASS for aggregate success. A separate supplemental pass
does not rewrite the historical aggregate, remove an entry or justify relaxing
acceptance. No inputs were installed or fetched, no external code/tests were
run, and no new raw logs were produced for this handoff.
