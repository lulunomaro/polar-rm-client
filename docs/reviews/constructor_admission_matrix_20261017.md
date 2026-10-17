# Bounded constructor-admission matrix

Production source `666c040`, test-only lane. No production change or new
production defect is claimed. Constructors are tested independently of existing
Thread.start rejection tests; native serial handles, pipes and sockets are inert.
Thread instances and the installed Paho client/resource cleanup are real.

Five new methods establish these specific missing cells:

| Owner / rejected construction | Required observed result |
| --- | --- |
| Manager serial starter | No serial open/start; failed lifecycle and E-stop remain explicit; final shutdown finishes without a phantom starter. |
| Serial reader, after handle opens | Successful native close releases handle; failed close retains exactly that handle, vetoes reopen, refuses strict success and releases only on explicit retry. |
| Each of three decoder pipe workers | No unstarted worker is treated as admitted. Process and all pipes close, or a failed pipe remains with the retained session; replacement is vetoed until explicit retry succeeds. Six constructor/close combinations. |
| Actual Paho network thread | Both already-created wakeup sockets close exactly once, no client remains published, constructor error remains observable. |
| MQTT cleanup thread | Original constructor exception propagates, client remains owned and unpublished, stop observation is false, start does not replace it, explicit stop retry finishes. |

32 methods pass with neighboring serial/Paho/decoder/cleanup admission suites;
static quality gate and diff check pass. The standalone new suite runs five
methods, without accidentally rediscovering the imported fixture TestCase.
Three in-memory no-op admission mutations (serial, decoder, Paho) are rejected by
the new assertions. No source/baseline was modified to run negative controls.

The initial harness replaced threading.Thread with a function during the Paho
case, invalidating production isinstance checks. It also directly imported a
TestCase, causing duplicate discovery. Its failing log is retained as
`fixture-error.log`, **not** red production evidence. The correction uses a real
Thread subclass for that case and a module-qualified fixture reference. No
production assertion was relaxed; constructor/error/native-owner assertions are
stronger than merely observing an exception.

Remaining scope is explicit: this matrix covers the named constructor sites,
not every Python allocation or native-library constructor; late/admitted starts
remain covered by separate existing suites. No socket connect/bind, hardware,
codec process or native display runs. Existing offscreen/lifecycle diagnostics
in neighbor output remain unmodified. This evidence cannot relabel the
`93aa839` full-profile NO-GO or resolve the window activation timeout.

Evidence: `../evidence/clean-code-20261017/constructor-admission-matrix/`.
Reproduce with the pinned Python and `PYTHONPATH=.:tests`; run
`python -m unittest test_constructor_admission_matrix` and the retained
`mutation_controls.py CHECKOUT_PATH`. Independent review and final integration
remain required before treating these cells as accepted at the combined source.
