# Physical simulated projectile to original-Qt hit-spin check

The capability-qualified AutoSpinOnHit increment has now completed a bounded
physical-simulation integration check, in addition to synthetic and CPU contracts.
The client and bridge code were frozen before the run.

The explicit test fixture used one incoming test shot from another simulated
robot. The normal ammunition/fire gate, muzzle launch, flight/contact path and
judge's actual credited HP removal were retained. The test did not directly call
a judge hit function or modify the controlled robot's HP. The controlled robot
was outside supply healing, and its own fire command stayed false throughout.

The original Qt module and input path established a live baseline before that
shot. A positive authoritative damage observation then led to an `on_hit` spin
request, matching effective feedback and measured physical yaw motion. The policy
expired using simulation time and the chassis braked. Repeating the cumulative
counter did not trigger again; focus loss neutralized input, and disconnect did
not resume it. The test-shot trigger was one-use and required a live input lease.

This is one explicitly initialized scenario, not every projectile/armor angle,
robot, multiple-hit load, full match or real-device qualification. Firing remains
manual or an explicit test-driver action; neither source aiming nor AutoSpinOnHit
receives automatic firing authority. Power/buffer protection remains unavailable.

## Reproduce

Use a separately installed simulation-only physical-hit helper. Its stdout ready
record supplies the loopback `url`, fresh one-use `trigger_file`, controlled robot,
shooter and frozen source identities. It records `physics_evidence.json` next to
the trigger, including muzzle launches, swept contacts, credited judge impacts,
ammunition, ownership and measured angular velocity. Do not substitute direct
judge-event injection for this helper.

Create a **local** JSON launch description with `argv` and `cwd`, then run:

```sh
QT_QPA_PLATFORM=offscreen python tools/verify_sim_hit_spin.py \
  --client-root . --bridge-command-file <local-launch.json> --out <private-output>
```

The script starts a child bridge and uses the original client window and input
paths. No serial, DT7, physical MQTT or hardware service is constructed. Verify
that the helper's single launched UID equals its contact and impact UID, and that
the impact event sequence/actual credited damage agree with the client's
`own_damage` and observed HP change. Keep complete negative results if a check
fails; do not replace a failed physics path with a fabricated hit event.

The screenshot is taken after the measured response and safe stop, with separate
pre-paint and post-save telemetry. It is a CPU rendering of the original HUD plus
real simulator camera pixels. It does not establish desktop/OpenGL rendering.
Known missing/fragmented glyphs and bitmap-text artifacts are still visual defects;
images are retained without retouching. Actual records, screenshots and local
launch paths belong in private evidence storage, not this public repository.

## Entry safeguards

The publication wrapper additionally rejects optimized Python (`-O`, `-OO` or
`PYTHONOPTIMIZE`) because assertions are acceptance checks. It requires a fresh,
nonexistent output directory before loading the launch command or starting the
bridge, so an earlier failure or evidence bundle cannot be overwritten. These
entry safeguards have separate CPU subprocess tests; the complete physical run
above used the original script before these guards and has not been rerun merely
to qualify wrapper startup. Run the entry tests with:

```sh
python -m unittest discover -s tests -p test_sim_hit_spin_entry.py
```
