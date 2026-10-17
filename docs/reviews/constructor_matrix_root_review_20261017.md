# Independent integration-owner review

Candidate `060f190` reviewed in a separate detached worktree. The reviewer read
all five test methods and the three mutation controls, then independently ran:

```sh
CI=true QT_QPA_PLATFORM=offscreen PYTHONPATH=.:tests "$PYTHON" -m unittest test_constructor_admission_matrix
CI=true QT_QPA_PLATFORM=offscreen PYTHONPATH=.:tests "$PYTHON" docs/evidence/clean-code-20261017/constructor-admission-matrix/mutation_controls.py .
```

Five methods passed. No-op serial, decoder and Paho admission candidates were
rejected with respectively 2, 6 and 1 assertion failures and zero errors. The
checks require positive owner acquisition/error handling, retained failed-close
ownership, replacement veto and explicit retry, rather than only an exception.

The Paho constructor uses a real Thread subclass, preserving production
isinstance semantics. Fake sockets/pipes/native handles are explicit; no device
or network connection claim. Shared fixture imports are a test dependency and
not an independent oracle for their implementations. This review accepts the
named constructor cells only. It does not close the prior integrated timeout,
certify all constructors or replace final-source aggregate verification.
