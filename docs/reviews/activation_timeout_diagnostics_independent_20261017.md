# Independent activation timeout diagnostics review

Exact diagnostic candidate `893faa38cc1bff9bfa3c8dd86dc40e1abacdc748`, separate
worktree. **Accepted as diagnostics only; original timeout remains unresolved.**

The subprocess now periodically dumps Python stacks, flushes its final JSON and
prints both captured streams before re-raising a timeout. The earlier draft's
Python-3.11-only `add_note` call was replaced by stderr output, preserving the
project's Python 3.10 support. The 30-second timeout and existing retirement,
network-blocking and multiwindow assertions are unchanged.

Independent execution passes the five activation methods. An inert timeout
negative control disables `add_note`, supplies stdout/stderr bytes, and verifies
both streams are reported, the exact original TimeoutExpired object propagates,
the subprocess timeout remains 30, and stack-dump instrumentation is present.
This control tests the failure path without sleeping or invoking a native app.

Neither this successful run nor the author's repeated probes proves the original
`93aa839` timer-factory timeout was fixed. Its cause was not reproduced here.
The original aggregate remains NO-GO; any subsequent green aggregate must identify
its own source and cannot erase this historical intermittent failure.

Evidence: `../evidence/clean-code-20261017/activation-diagnostics-independent/`.
The unrelated audio-executor retirement investigation has separate red/fix
candidates and must not be represented as this timeout's cause without evidence.
