# Manual control closure, server-window profile

The v2 Qt operator requests `server_window_v1`. A server that cannot acknowledge
this profile cannot silently become an operator endpoint. Spectator/watch remains
read-only. The default generic SDK profile is `legacy_v2` for compatibility; it
has receive-relative TTL and is **not** covered by the pre-arrival delay guarantee.

`POST /v2/control_window` takes robot_id and exact owned lease_epochs. Its reply
binds session_id, robot_id, lease_epochs and a monotonically increasing window_id
to issued_wall/expires_wall on the **server monotonic clock**. The default lifetime
is 150 ms, an engineering setting, not a measured LAN guarantee. No client/server
absolute clocks are compared. Each session/robot stores one current window.
Replacement does not extend already admitted inputs. Bind, unbind, release,
episode/lease changes and disconnect invalidate applicable authority.

Strict `/v2/control` adds window_id. The admitted expiry is the earlier of the
original window deadline and receive time + valid_for_s. Sequence and identity
checks remain mandatory. The physics path rechecks authority before actuator write,
at its sampled common integration boundary, and before the discrete fire operation.
Expired fire cannot decrement ammo/add heat/create a projectile. This is validation
at sampled consumption boundaries, not an operating-system hard-real-time promise.

The Qt control worker prefetches a window near expiry; the GUI never waits on that
HTTP exchange. A new window requires a fresh ordinary input sample **after** its
ACK. Local conservative expiry is request_start + acknowledged lifetime. Neutral,
focus loss and connection loss revoke pending work. Already transmitted packets
cannot be recalled instantaneously; their original server deadline still bounds
acceptance/consumption. Late state/decode/open/claim completions cannot revive
closed or superseded display/control generations.

## Reproduce CPU and loopback tests

Server, from the simulation Python root:

    python -m unittest rm_match.platform_v2.test_control_windows rm_match.platform_v2.test_window_client rm_match.platform_v2.test_window_consumption

Client (with Qt/OpenCV dependencies, repository root):

    PYTHONPATH=.:tests QT_QPA_PLATFORM=offscreen python -m unittest test_platform_display_generation test_platform_control_windows test_sim_platform_v2

The environment-variable syntax above is POSIX; on PowerShell set `$env:PYTHONPATH`
and `$env:QT_QPA_PLATFORM` first. No Windows execution qualification is implied.

Bounded original-input/real-physics no-render test, from the client repository:

    python tools/verify_platform_control_loopback.py --nav-root <simulation-python-root> --nav-python <simulation-python-executable> --out <new-output-directory>

This invokes `tools/platform_v2/control_window/serve_no_gl.py`. It uses actual
original Qt event/input classes, loopback HTTP, MuJoCo, judge and UI model mapping.
Its camera gate is an explicitly synthetic 8x6 JPEG test fixture. It does not prove
camera rendering, visible desktop interaction, cross-machine networking or a full
match. Both scripts reject optimized Python and existing output directories.
Production launchers never enable the synthetic gate. Real-time and offline/SIL
profiles remain separate. No native autoaim, physics parameters or visual design
are changed by this manual-control checkpoint.
