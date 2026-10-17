# Bridge loopback evidence validity

Base: `ade481f6b843f983346362b501c81a2603e4fe0b`.
This changes only the test harness and its negative controls; bridge/transport
production behavior, verifier classification and registry metadata are unchanged.

## Two reproduced evidence defects

1. The transport loopback used `board.closed is True or True`, which always
   passed. It also discarded the bridge server thread, so setting a stop flag
   and waiting 200 ms could not establish ownership retirement. A subprocess
   replacing `Board.close` with a no-op still reported success.
2. The 13-versus-14 historical assertion count was a logging race, not evidence
   of a different source suite. The retained wave4 stdout contains a bridge
   peer-admission message immediately followed by `[ OK ] Stub...` on the same
   line. The verifier correctly counts only anchored assertion lines. A small
   classifier reproduction confirms that merged text loses one counted check.
   The original log remains at
   `../evidence/clean-code-20261016/secondpass-wave4/logs/tools__test_car_bridge_transport.stdout.log`.

## Bounded correction

`start_bridge` retains and returns its server thread. After stopping the client,
the harness requests bridge stop and joins that thread for at most two seconds.
A cleanup assertion passes only if the server is no longer alive **and** the fake
serial board's close flag is true. A live server cannot be hidden by a closed
board, and an exited server cannot hide a no-op serial close.

Both loopback instances are checked. The second instance previously had no
cleanup assertion; the corrected script therefore has **15 actual checks**, not
14. Background bridge diagnostics and foreground script output now share one
output lock, keeping assertion lines independently countable. No broader regex,
count override, waiver, hardware claim or completion shortcut was added.

## Evidence

- Test-first `cb2a64a`: three methods, two failures (no-op close wrongly passes;
  the second loopback has no cleanup check). The logging classifier control
  already passes and explains the historical discrepancy.
- Correction `3f4cc3d`: four methods pass, including the additional bounded-join/
  still-live-server negative control.
- A real localhost/fake-board run passes all 15 assertions; ten independent
  subprocess repetitions each classify PASS with exactly 15 assertions.
- A corrected no-op-board-close mutation exits 1, with both cleanup checks FAIL.
- Both `tools/test_car_bridge_transport.py` and its new evidence suite are
  automatically discovered as offline. Verifier, registry and quality baseline
  are unchanged. No `rm_client` source changed.

Raw logs and hashes are under
`../evidence/clean-code-20261016/bridge-evidence-validity/`; gzip preserves their
exact bytes. Reproduce with:

```sh
python tests/test_car_bridge_evidence.py
python -u tools/test_car_bridge_transport.py
```

This establishes bounded retirement for the local test server and fake board.
It does not qualify real serial handles, physical firmware neutralization,
wireless conditions or hardware shutdown. An unrelated early exception can still
interrupt the script before its explicit cleanup stage; that existing path relies
on subprocess teardown and is not claimed as bounded in-process cleanup. Historical 13/14-count reports remain
historical; they are not rewritten into corrected cleanup evidence. The
parent-owned fixed-source aggregate for this correction remains separate.
