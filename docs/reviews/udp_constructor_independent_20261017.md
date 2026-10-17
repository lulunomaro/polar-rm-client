# Independent UDP constructor-admission review

Fixed candidate `8a354c065fb86ad27905eed8e423df9d881c039f`, separate detached
worktree. **PASS for the narrow correction.**

The original `6b9481e` red assertion now passes: rejected thread construction
preserves exception identity, opens no socket, clears the phantom running/session
state and allows a later start to reach admission. Construction now sits inside
the existing admission handler; a missing thread is treated as never admitted.
The genuine admitted-thread identity branch is unchanged.

Independent focused selection passes 19 methods across constructor audit,
receiver admission, transport start matrix, video close ownership, source retry
and cleanup-worker admission. Diff check passes. Tests were not relaxed. These
checks exercise real ownership bookkeeping with fake native boundaries and do
not certify live UDP traffic or native decoder behavior.

Raw output: `../evidence/clean-code-20261017/udp-independent/focused.log`.
Final combined-source verification remains owned by the integrating lane.
