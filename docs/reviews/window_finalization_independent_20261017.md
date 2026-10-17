# Independent corrected window-finalization review

Fixed candidate `fb271c81bd1c09b8f32b886a5e7a4dd91fb8690f` in a separate worktree.
The earlier `a5746b4` eager callback resolution changed original seal/failure
prefixes; this correction is accepted for the reviewed boundary.

Independent results:

- 51 methods pass across `test_window_finalization_contract`,
  `test_window_finalization_owner`, `test_window_lifecycle_controller` and
  `test_window_async_lifecycle` using the pinned verification interpreter.
- 26 independent differential cases compare the real current adapter AST with
  `_finish_window_shutdown` at `54650e4`: each of 13 collaborator lookups raises
  in turn, for context absent/present. Exact effect traces, exception type/text
  and finalized state match in all cases.
- The full base-to-candidate diff check passes. No native GL was exercised.

The correction resolves prepare callbacks after sealing, `makeCurrent` in the
entry phase, and `doneCurrent` in the finally phase. Missing-context callbacks
are not eagerly resolved. Lookup failure remains distinct from invocation
failure; the latter is collected by `run_owner_actions`. Existing owner error
ordering and warning delivery remain unchanged. The author added dedicated
reentrant-lookup and missing-callback cases rather than loosening the oracle.

This accepts finalization extraction, not earlier partial-constructor ownership,
physical resource retirement or native desktop behavior. Raw focused output and
independent lookup comparison result are in
`../evidence/clean-code-20261017/window-independent/`.
