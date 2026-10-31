"""Original EndfieldHUD scene owner; no CPU render/copy in ordinary frames.

The bounded native worker owns D3D/DirectComposition, its source clocks and
private fonts. Qt owns modal input and every semantic client action. No native
callback can publish a robot command or arm match control.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
from pathlib import Path
import time

_LIBRARIES: dict[str, object] = {}  # Never unload code while a worker retires.


def library_path(pack: Path) -> Path:
    try:
        return _library_path(pack)
    except FileNotFoundError as error:
        raise FileNotFoundError(
            "Endfield 菜单资源未完整准备。请按 docs/endfield/PACK_SETUP.md 构建完整资源包，"
            "再运行 python tools/endfield/prepare_pack.py check --pack <user_data/endfield_pack>。"
            "不要新建空 BUILD_MANIFEST.json。缺少：" + str(error.filename)
        ) from error


def _library_path(pack: Path) -> Path:
    root = pack / "native_renderer"
    active = root / "CURRENT.json"
    if active.is_file():
        data = json.loads(active.read_text(encoding="utf-8"))
        name = data["directory"]
        if not isinstance(name, str) or not re.fullmatch(r"build_[0-9a-f]{16}", name):
            raise ValueError("Invalid source renderer version directory")
        root /= name
    manifest = json.loads((root / "BUILD_MANIFEST.json").read_text(encoding="utf-8"))
    if manifest["reference_commit"] != "cdbd4f573c8fa3b9c2048fa3d1b089e69196537b":
        raise ValueError("Source renderer revision differs")
    library = root / "polar_endfield.dll"
    with library.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != manifest["binary_sha256"]:
            raise ValueError("Source renderer hash differs")
    return library


class NativeApi:
    def __init__(self, path: Path):
        key = str(path.resolve())
        if key not in _LIBRARIES:
            lib = ctypes.CDLL(key)
            u64, uint, number, ptr = ctypes.c_uint64, ctypes.c_uint, ctypes.c_double, ctypes.c_char_p
            signatures = {
                "create": ([ctypes.c_size_t, ptr, uint, uint, ctypes.c_int], u64),
                "status": ([u64], ctypes.c_int), "phase": ([u64], ctypes.c_int), "action": ([u64], ctypes.c_int),
                "visible": ([u64, ctypes.c_int], None), "pause": ([u64, ctypes.c_int], None),
                "resize": ([u64, uint, uint], None), "input": ([u64, ctypes.c_int, number, number, number], None),
                "context": ([u64, ptr], None), "error": ([u64], ptr), "stats": ([u64, ctypes.POINTER(number)], None),
                "destroy": ([u64], None), "release": ([u64], None), "capture": ([u64, ptr], None),
                "captured": ([u64], ctypes.c_int),
                "button": ([u64, ctypes.c_int, ctypes.POINTER(number)], ctypes.c_int),
            }
            for name, (arguments, result) in signatures.items():
                function = getattr(lib, "pe_" + name)
                function.argtypes, function.restype = arguments, result
            if hasattr(lib, 'pe_device_info'):
                lib.pe_device_info.argtypes = [u64]
                lib.pe_device_info.restype = ptr
            if hasattr(lib, 'pe_frame_rate'):
                lib.pe_frame_rate.argtypes = [u64, uint]
                lib.pe_frame_rate.restype = None
            _LIBRARIES[key] = lib
        self.lib = _LIBRARIES[key]

    def __getattr__(self, name):
        return getattr(self.lib, "pe_" + name)


class EndfieldNativeMenu:
    def __init__(self, pack: Path, *, renderer_pack: Path | None = None):
        self.pack = pack
        # Executable code belongs to this application revision. An older
        # user_data pack may still supply the same pinned scene/resources,
        # but must not silently shadow a shipped native composition fix.
        self.renderer_pack = renderer_pack if renderer_pack is not None else pack
        self.api = None
        self.handle = 0
        self.error = ""
        self.requested = False
        self.attempted = False
        self.retiring = False
        self._awaiting_backdrop = False
        self._backdrop_frame_ready = False
        self._context = None
        self._size = None
        self._frame_limit = None

    def prepare(self, parent: int, width: int, height: int, *, offscreen=False) -> None:
        if self.attempted or self.retiring:
            return
        self.attempted = True
        try:
            self.api = NativeApi(library_path(self.renderer_pack))
            self.handle = int(self.api.create(parent, os.fsencode(self.pack / "native_source_runtime"), width, height, int(offscreen)))
            self.sync_frame_limit()
            if not self.handle:
                raise RuntimeError("Could not create source renderer owner")
            self.api.visible(self.handle, self.requested and not self._awaiting_backdrop)
        except (OSError, ValueError, KeyError, RuntimeError, AttributeError) as error:
            self.error = str(error)

    @property
    def ready(self) -> bool:
        return bool(self.handle and self.api.status(self.handle) == 1)

    @property
    def phase(self) -> int:
        if self._awaiting_backdrop:
            return 3
        return self.api.phase(self.handle) if self.handle else 0

    def request(self, visible: bool, *, wait_for_backdrop=False) -> None:
        self.requested = visible
        self._awaiting_backdrop = bool(visible and wait_for_backdrop)
        self._backdrop_frame_ready = False
        if self.handle:
            self.api.visible(self.handle, visible and not self._awaiting_backdrop)

    def backdrop_frame_ready(self, accepted: bool) -> None:
        self._backdrop_frame_ready = bool(accepted and self.requested)

    def backdrop_presented(self) -> None:
        # Reveal the native visual only after a successful Esc backdrop has
        # actually reached Qt's swap, including when reversing an exit clip.
        if self._awaiting_backdrop and self._backdrop_frame_ready and self.handle and not self.retiring:
            self._awaiting_backdrop = False
            self.api.visible(self.handle, True)

    def tick(self, *, width: int, height: int, chinese: bool, mode: str, paused: bool) -> int:
        if not self.handle or self.retiring:
            return 0
        self.sync_frame_limit()
        status = self.api.status(self.handle)
        if status == -1:
            self.error = self.api.error(self.handle).decode("utf-8", "replace")
            return 0
        size = (width, height)
        if size != self._size:
            self.api.resize(self.handle, width, height)
            self._size = size
        context = (bool(chinese), mode)
        if context != self._context:
            self.api.context(self.handle, json.dumps({"chinese": chinese, "mode": mode}).encode("utf-8"))
            self._context = context
        self.api.pause(self.handle, paused)
        # 模态期间丢弃旧点击，避免关闭子窗口后重放并再次打开它。
        action = self.api.action(self.handle)
        return action if not paused else 0

    def pointer(self, kind: int, x: float, y: float, value=0.0) -> None:
        if self.ready:
            self.api.input(self.handle, kind, x, y, value)

    def close(self) -> None:
        self.retiring = True
        self.requested = False
        self._awaiting_backdrop = self._backdrop_frame_ready = False
        if self.handle:
            self.api.destroy(self.handle)

    def wait_closed(self, timeout=5.0) -> None:
        if not self.handle:
            return
        deadline = time.monotonic() + timeout
        while self.api.status(self.handle) not in (-1, 2):
            if time.monotonic() > deadline:
                raise TimeoutError("Source render owner did not retire")
            time.sleep(.02)
        self.api.release(self.handle)
        self.handle = 0

    def stats(self) -> dict:
        values = (ctypes.c_double * 5)()
        if self.handle:
            self.api.stats(self.handle, values)
        return dict(zip(("frames", "last_ms", "textures", "meshes", "owners"), values))

    def device_info(self) -> dict:
        if not self.handle or self.api is None:
            return {}
        try:
            value = self.api.device_info(self.handle)
            if not value:
                return {}
            if len(value) > 8192:
                raise ValueError('oversized device diagnostics')
            parsed = json.loads(value.decode('utf-8'))
            if not isinstance(parsed, dict):
                raise ValueError('invalid device diagnostics')
            return parsed
        except (AttributeError, ValueError, TypeError) as exc:
            return {'selection_note': 'Device diagnostics unavailable: '+str(exc)}

    def sync_frame_limit(self):
        if not self.handle or self.api is None:
            return
        from rm_client.ui.cs2_video_settings import render_target
        limit = int(render_target(144))
        if self._frame_limit != limit:
            try:self.api.frame_rate(self.handle,limit)
            except AttributeError:return  # Explicit older developer DLL.
            self._frame_limit = limit
