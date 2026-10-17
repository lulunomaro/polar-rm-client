# CC port-conflict falling-edge correction

This is an intentional behavior correction before the device-egress extraction,
not a claim of equivalence with the old manager.

An active CC writer could hold non-neutral keys on an old port while a requested
replacement port was still owned by the controller-angle relay. The conflict
branch disabled `cc_enabled` and cleared `_cc_was_enabled`, then returned before
requesting neutral from the old CC writer. A later worker watchdog was the only
fallback for that missed explicit falling edge.

The conflict branch now revokes local output, retains/reconnects the old writer
reference, and requests neutral before publishing the conflict diagnostic. It
also recognizes a retained non-neutral writer when the prior enable flag or
control-side reference was already lost. Unconfirmed neutral remains pending.
Repeated configuration polls do not replenish the worker's bounded retry budget;
no competing writer is created and no close/retry implementation is changed.

`tests/test_device_egress_safety.py` uses the actual asynchronous CC writer and
control bridge with a fake byte sink. It first writes non-neutral W, then drives
the conflicting replacement branch. The clock is frozen so a later watchdog
cannot mask a missing manager release. Cases cover a blocked neutral write,
confirmed neutral, failed neutral with an exhausted budget, and lost prior flags
or control references. All three methods failed before the correction and pass
after it.

Focused verification also passed: controller-angle integration (14 methods),
CC worker dispatch (8), strict writer-close ownership (7), and transport safety
regressions (36). No serial device or native hardware was involved.
