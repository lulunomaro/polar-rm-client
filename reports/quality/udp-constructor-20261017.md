# UDP receiver constructor admission repair

The audit regression `6b9481e` was cherry-picked as `8e63868` and reproduced:
`UDPVideoReceiver.start()` published `running=True` and a session before creating
the receive thread, but thread construction lay outside its failure handler.
Construction rejection left a phantom owner and silently prevented later starts.
No socket was acquired in this failure path.

The existing admission try block now includes thread construction/publication.
Its rejection handler clears a session whose thread is absent or never admitted;
late-start failures continue retaining any genuinely admitted thread/session.
The original exception is re-raised unchanged. Stop, native socket cleanup,
callback delivery, frame ordering, timeout and packet protocols are unchanged.

Reproduction uses Python 3.12.14 with `QT_QPA_PLATFORM=offscreen CI=true PYTHONPATH=.`:

- `python tests/test_udp_constructor_admission_audit.py`: failed before repair
  (`receiver.running` remained true), passes afterward; confirms original failure
  identity, no socket acquisition, cleared owner, and retry reaching constructor.
- `python -m unittest discover -s tests -p 'test_udp*py'`: 7 pass, including
  late-start ownership and nonblocking stop cases.
- `python -m unittest discover -s tests -p test_transport_start_admission_matrix.py`:
  2 pass.
- `python tools/quality_gate.py`: unchanged baseline passes; 216 modules, 3405
  functions, zero initialization cycles.
- `git diff --check`: pass.

All socket/thread failure evidence uses test doubles. Full aggregate verification
belongs to the integrating checkout; no live UDP/native-driver qualification.
