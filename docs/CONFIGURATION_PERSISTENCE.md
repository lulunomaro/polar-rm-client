# Configuration persistence boundaries

## Accepted identity and unsaved drafts

The profile selector is a UI choice. It is not evidence that the live registry
belongs to that file. `ProfileIdentity(name, generation)` records the last
successfully accepted load or explicit save. Autosave requests retain that
identity; a superseded generation cannot write the newer live registry, even
when the file name is unchanged.

Before switching to a different profile, pending edits are saved to their
accepted source. A failed source save cancels the switch and leaves a retry
pending. The target is accepted only after the matching backend lifecycle
completion succeeds. Failed activation and close cancellation restore the old
selection together with the old model. They do not replay held input or arm
outputs.

An explicit save to a new selector name remains the existing save-as entry.
If it fails, its live draft and chosen name are retained. Implicit saving stays
blocked until an explicit save succeeds or another document loads successfully.
Missing/invalid loads cannot silently release this protection and overwrite the
source. A further explicit save may choose a third name or return to the source.
A failed draft remains in memory; this is not crash recovery for unsaved edits.

Before beginning normal shutdown, `prepare_configuration_for_close` retries that
explicit target once, or saves ordinary dirty data to its accepted profile owner.
`configuration_close_requires_save` exposes this admission need to the window so
it can lock control and clear input before potentially fallible disk work. A
failed retry refuses close and retains the editable draft and runtime owners;
control stays locked and held input is not replayed. A pending configuration/runtime
transition also defers that retry so staged values cannot be written as the
failed copy. There is no background retry loop for the copy. The user can keep
editing after cancelling close, explicitly save again, or load another document.
A disk failure during this admission also pauses ordinary automatic flushes.
Dirty values and their owner remain intact; explicit Save or another close
attempt retries once. Success or successful loading of another document clears
the pause. The admission method creates no retry timer; ordinary autosave
failures outside close retain their existing retry policy. The window must call admission
before changing its closing state.

When dirty data exists during a pending load or restoration, close is explicitly
deferred with a retry-after-transition message. The pending transaction and its
rollback snapshot remain owned by normal completion routing; refusal does not
cancel them. This includes reloading the same profile name while its previous
live edits await autosave. Successful explicit reload accepts the loaded document
and invalidates the old save generation. Failed activation restores the previous
dirty document and save owner. A subsequent close saves that restored document,
and a further disk failure keeps it editable under the same manual-retry policy.
An ordinary runtime transition can also finish before the user retries close.

The public `save_current_config` and autosave flush methods reject a pending
profile transaction, but are not general lifecycle admission APIs. Direct
programmatic calls may persist the current registry during an ordinary scenario
transition. Production window input admission blocks the interactive save action,
and its tick polls lifecycle before reaching autosave, so these normal UI paths
do not write while that transition is pending. Other callers must provide their
own admission if they require the same ordering. Import and close admission
explicitly check ordinary transitions as well.

Import can replace the currently accepted file. Its old pending edits are saved
before replacement, so the existing import backup contains those latest edits
and an old autosave timer cannot rewrite the imported file after failed
activation. Existing user profiles are not deleted by profile selection.

## Profile plus startup preference writes

`ClientConfigStore.save_with_startup` is a single-writer, in-process operation:

1. Preflight values and snapshot the exact prior bytes or absence of both files.
2. Save the profile, then save `.startup.json`, using the existing individual
   atomic replacement operations.
3. On an ordinary caught exception, attempt both restorations independently in
   reverse order. Existing files recover their original bytes, including unusual
   formatting or corrupt legacy contents. Only a file absent before this save is
   removed during rollback.
4. Report the original exception and every failed restoration through
   `ProfileSaveError`. An incomplete recovery is always visible in the window,
   even if an earlier autosave failure was already announced. The latest
   incomplete-recovery diagnostic stays in session state and is logged with its
   exception chain; a later successful retry does not erase that evidence.

This is **not a cross-file crash-atomic transaction**. Another reader can see an
intermediate pair, and process termination can prevent rollback. It also does
not coordinate concurrent external writers. Single-file replacement and caught
I/O recovery must not be described as power-loss consistency for the pair.

The existing `save` and `save_startup_preferences` methods remain available for
callers that intentionally save only one file. Stored profile schemas and the
startup preference shape are unchanged.

## Reproduction

- `python tests/test_profile_file_transactions.py` uses temporary files and is
  Qt-free. It exercises failed first/second writes, failure after replacement,
  exact bytes, original absence, independent recovery attempts, validation and
  successful saves.
- `QT_QPA_PLATFORM=offscreen POLAR_DISABLE_LOCAL_CAPTURE=1
  POLAR_DISABLE_MEDIA_PRELOAD=1 python tests/test_profile_save_identity.py`
  exercises real window methods with temporary user-data roots and injected
  failures. It covers accepted identity, stale generations, failed/successful
  selection, close cancellation, explicit save-as, same-name import, failed
  startup writes and visible incomplete recovery.
- `tests/test_profile_close_pending.py` calls the actual window close adapter
  while controlled teardown workers delay same-name loads, failed activation,
  asynchronous restoration and ordinary scenario changes. It verifies no staged
  writes or premature snapshot cancellation, visible deferral, and successful
  later close. `tests/test_window_async_lifecycle.py` covers idle dirty/failed-copy
  admission and save-before-native-shutdown ordering.

These checks do not exercise physical devices, native OpenGL presentation,
process-kill durability, or multi-process writers.
