"""Read-only DXGI inventory and honest WGL/D3D device diagnostics.

Qt 6 QOpenGLWidget uses WGL, not Qt Quick's D3D adapter setting. The explicit
adapter choice applies to our native Esc D3D device. WGL routing remains with
Windows/the display driver; never claim that saving a LUID changed that context.
No registry edits, shell probes, game hooks or enumeration in paint/tick.
"""
from __future__ import annotations

import ctypes as C
from dataclasses import dataclass
from functools import lru_cache
import os
import re
import sys
import uuid


@dataclass(frozen=True)
class GraphicsAdapter:
    id: str
    name: str
    dedicated_bytes: int = 0


def valid_adapter(value):
    return isinstance(value, str) and (value == 'auto' or
        re.fullmatch(r'luid:[0-9a-f]{8}:[0-9a-f]{8}', value) is not None)


def _dxgi_inventory():
    if sys.platform != 'win32':
        return ()
    class Luid(C.Structure):
        _fields_ = [('low', C.c_uint32), ('high', C.c_int32)]
    class Description(C.Structure):
        _fields_ = [('name', C.c_wchar * 128), ('vendor', C.c_uint32),
            ('device', C.c_uint32), ('subsystem', C.c_uint32), ('revision', C.c_uint32),
            ('video', C.c_size_t), ('system', C.c_size_t), ('shared', C.c_size_t),
            ('luid', Luid), ('flags', C.c_uint32)]
    def method(ptr, index, result, *args):
        address = C.cast(ptr, C.POINTER(C.POINTER(C.c_void_p))).contents[index]
        return C.WINFUNCTYPE(result, C.c_void_p, *args)(address)
    factory = C.c_void_p()
    dll = C.WinDLL('dxgi.dll')
    create = dll.CreateDXGIFactory1
    create.argtypes = [C.c_void_p, C.POINTER(C.c_void_p)]
    create.restype = C.c_int32
    iid = C.create_string_buffer(uuid.UUID('770aae78-f26f-4dba-a829-253c83d1b387').bytes_le)
    if create(iid, C.byref(factory)) < 0:
        raise OSError('CreateDXGIFactory1 failed')
    adapters = []
    try:
        enum = method(factory, 12, C.c_int32, C.c_uint, C.POINTER(C.c_void_p))
        for index in range(32):
            adapter = C.c_void_p()
            result = enum(factory, index, C.byref(adapter))
            if result & 0xffffffff == 0x887a0002:  # DXGI_ERROR_NOT_FOUND
                break
            if result < 0:
                raise OSError('DXGI adapter enumeration failed')
            try:
                description = Description()
                if method(adapter, 10, C.c_int32, C.POINTER(Description))(adapter, C.byref(description)) < 0:
                    raise OSError('DXGI GetDesc1 failed')
                if not description.flags & 2:  # Never offer WARP/software as hardware.
                    key = f'luid:{description.luid.high & 0xffffffff:08x}:{description.luid.low:08x}'
                    adapters.append(GraphicsAdapter(key, description.name, description.video))
            finally:
                method(adapter, 2, C.c_uint32)(adapter)
    finally:
        method(factory, 2, C.c_uint32)(factory)
    return tuple(adapters)


@lru_cache(maxsize=1)
def graphics_inventory():
    try:
        return _dxgi_inventory(), ''
    except (OSError, ValueError, AttributeError) as exc:
        return (), f'{type(exc).__name__}: {exc}'


def adapter_rows(selected):
    adapters, error = graphics_inventory()
    rows = [('自动 / 系统默认', 'auto')]
    rows.extend((f'{a.name} [{a.id[-8:]}]', a.id) for a in adapters)
    if selected != 'auto' and all(a.id != selected for a in adapters):
        rows.append(('未检测到已选显卡（将诊断并回退）', selected))
    return rows, error


def configure_process_graphics(selected):
    """Before QApplication/native owners; saving a draft does not call this."""
    if not valid_adapter(selected):
        raise ValueError('无效渲染显卡标识')
    os.environ['POLAR_RENDER_ADAPTER'] = selected


def device_summary(host):
    diagnostics = getattr(host, 'render_runtime', None)
    snapshot = diagnostics.snapshot() if diagnostics is not None else {}
    gl = snapshot.get('driver', {})
    native = getattr(host, '_endfield_menu', None)
    native_info = native.device_info() if native is not None else {}
    gl_name = gl.get('renderer') or '上下文尚未初始化'
    native_name = native_info.get('name') or '尚未打开 Esc / 原生设备未初始化'
    note = native_info.get('selection_note', '')
    native_error = getattr(native,'error','') if native is not None else ''
    if native_error:note += ' Esc 初始化/呈现错误：'+str(native_error)
    cadence = native_info.get('frame_limit')
    if cadence:note += f'（呈现上限 {cadence} FPS）'
    registry = getattr(host, 'registry', None)
    camera = registry.get('CameraFeed') if registry is not None else None
    source_setting = camera.get_setting('Source') if camera is not None else None
    source = str(source_setting.value) if source_setting is not None else '未知'
    return (f'主窗口 OpenGL / WGL：{gl_name}\n{gl.get("implementation", "")}\nEsc Direct3D 11：{native_name} {note}\n'
            f'GPU 合成帧：{snapshot.get("gpu_frames", 0)}；兼容帧：{snapshot.get("compatibility_frames", 0)}\n'
            f'最近兼容原因：{snapshot.get("last_fallback") or "无"}\n'
            f'当前视频源：{source}。压缩 H.264/HEVC 使用 FFmpeg/CPU；Webcam/Synthetic 不启动该解码器。未配置硬件解码GPU。')
