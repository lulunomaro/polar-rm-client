"""Offline red probe for an early-construction audio owner retention defect.

Exit 1 means a native Qt pool remains valid after its Python factory result is
lost. This is deliberately not an ordinary passing verification suite. It does
not demonstrate a physical audio-device handle leak or launch any transport.
"""
from pathlib import Path
import gc
import os
import sys
import tempfile
from types import SimpleNamespace
import wave
import weakref

os.environ.update(QT_QPA_PLATFORM="offscreen", CI="true", POLAR_DISABLE_MEDIA_PRELOAD="1")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication
import shiboken6
from rm_client.ui.polar.window_assembly import create_feedback_assets


def main() -> int:
    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        os.environ["POLAR_USER_DATA_ROOT"] = str(root / "user")
        cue = root / "ui_sounds/polar_classic/module_on.wav"
        cue.parent.mkdir(parents=True)
        with wave.open(str(cue), "wb") as sound:
            sound.setnchannels(1)
            sound.setsampwidth(2)
            sound.setframerate(8000)
            sound.writeframes(b"\x00\x00" * 80)
        paths = SimpleNamespace(user_data_root=root / "user",
            asset=lambda *parts: root.joinpath(*parts))
        references = []

        def interrupted_construction():
            feedback = create_feedback_assets(paths)
            references.extend((weakref.ref(feedback.ui_audio.pool), weakref.ref(feedback.kill_fx.sounds)))
            # A subsequent factory fails before the full-window abort contract.
            raise RuntimeError("subsequent service factory failed")

        try:
            interrupted_construction()
        except RuntimeError:
            pass
        for _ in range(10):
            gc.collect()
            app.processEvents()
            app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        pool = references[0]()
        retained = pool is not None and shiboken6.isValid(pool)
        print({"ui_pool_retained": retained,
               "kill_pool_retained": references[1]() is not None,
               "ui_effects": sum(len(items) for items in pool._effects.values()) if retained else 0})
        # Explicit fixture rescue, after observations, prevents the probe itself
        # from abandoning native QObjects at application teardown.
        if retained:
            pool.deleteLater()
            app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        return 1 if retained else 0


if __name__ == "__main__":
    raise SystemExit(main())
