# Python environment path preservation

The launcher now preserves the explicit Python invocation path. It expands `~` and makes relative paths absolute, without dereferencing a Python symlink or its parent directories. This matters because a virtual environment may link its interpreter to base Python; invoking the resolved base loses that virtual environment's prefix and dependencies.

The selected path must exist, refer to a bounded regular file and be executable. These checks do not prove it is a compatible Python or that the required packages/graphics/native libraries are installed. Plans and execution keep argument arrays with `shell=False`; spaces and shell metacharacters stay literal filename characters.

Source and asset hash paths retain their original canonicalization. No native, source, environment or lifecycle check is disabled. Manual and offline modes use the same corrected interpreter selection.

CPU tests from the client checkout root:

    PYTHONPATH=.:tests python -m unittest test_sim_launcher test_sim_launcher_venv -v

The added real process probe runs only standard-library Python inside a temporary environment created without pip or downloads. It checks `sys.prefix`, `sys.base_prefix` and `sys.executable`. This is not server, Qt window, physics, camera or Windows qualification.

Use the existing `tools/launch_sim_platform.py` plan first. Add `--run` only when the chosen environment has the required dependencies and a real local desktop. Never replace the explicit `--sim-platform-v2` route with the ordinary hardware entry.
