"""Offline Endfield pack integrity contract; never loads a DLL or installs assets."""
from __future__ import annotations

from array import array
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import stat
import struct
import sys

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = "ENDFIELD_PACK_MANIFEST.json"


def is_junction(path: Path) -> bool:
    """Python 3.11同样检查Windows目录联接，不以缺少Path新接口跳过边界校验。"""
    try:
        return getattr(path.lstat(), "st_reparse_tag", 0) == getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", 0xA0000003)
    except OSError:
        return False


def read_json(path: Path) -> dict:
    if path.stat().st_size > 1024 * 1024:
        raise ValueError(f"JSON exceeds pack budget: {path.name}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path.name}")
    return value


def sources() -> dict:
    return read_json(Path(__file__).with_name("endfield_pack_sources.json"))


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


ADAPTER_SOURCES = ("native/endfield/bridge.cpp", "native/endfield/catalog_extension.inc",
                   "native/endfield/adapter_selection.hpp")


def adapter_digest() -> str:
    """版本身份覆盖全部原生适配源码，防止只更新目录扩展却复用旧DLL。"""
    records = {name:digest(ROOT/name) for name in ADAPTER_SOURCES}
    return hashlib.sha256(json.dumps(records,sort_keys=True).encode()).hexdigest()


def adapter_canonical_digest() -> str:
    """Git换行转换不改变原生适配身份，两个源文件都必须匹配。"""
    records = {name:hashlib.sha256((ROOT/name).read_bytes().replace(b"\r\n",b"\n")).hexdigest()
               for name in ADAPTER_SOURCES}
    return hashlib.sha256(json.dumps(records,sort_keys=True).encode()).hexdigest()


def confined(root: Path, relative: str) -> Path:
    if (not isinstance(relative, str) or not relative or len(relative) > 512
            or any(c in relative for c in "\\:\0")
            or any(part in ("", ".", "..") or part.rstrip(" .") != part for part in relative.split("/"))
            or PurePosixPath(relative).is_absolute()):
        raise ValueError("Invalid pack-relative path")
    target = root
    for part in PurePosixPath(relative).parts:
        target /= part
        if target.is_symlink() or is_junction(target):
            raise ValueError("Pack links/junctions are not supported")
    return target


def snapshot(root: Path) -> dict[str, str]:
    if root.is_symlink() or is_junction(root):
        raise ValueError("Pack root cannot be a link/junction")
    files = {}
    total = 0
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        confined(root, relative)
        if path.is_file() and relative != MANIFEST:
            total += path.stat().st_size
            if len(files) >= 2048 or total > 192 * 1024 * 1024:
                raise ValueError("Pack exceeds file/byte budget")
            files[relative] = digest(path)
    return files


def validate_mesh(path: Path) -> int:
    if path.stat().st_size > 15 * 1024 * 1024:
        raise ValueError("Field mesh exceeds byte budget")
    raw = path.read_bytes()
    if len(raw) < 144 or raw[:8] != b"PRMESH01":
        raise ValueError("Invalid field mesh header")
    vertices, indices = struct.unpack_from("<II", raw, 8)
    if not 0 < vertices <= 360000 or not 0 < indices <= 360000 or indices % 3:
        raise ValueError("Field mesh exceeds native count budget")
    if len(raw) != 144 + vertices * 36 + indices * 4:
        raise ValueError("Invalid/truncated field mesh length")
    if not all(map(math.isfinite, struct.unpack_from("<16d", raw, 16))):
        raise ValueError("Nonfinite field transform")
    values, order = array("f"), array("I")
    values.frombytes(raw[144:144 + vertices * 36])
    order.frombytes(raw[144 + vertices * 36:])
    if sys.byteorder != "little":
        values.byteswap()
        order.byteswap()
    if not all(map(math.isfinite, values)) or max(order) >= vertices:
        raise ValueError("Invalid/nonfinite field mesh vertices or indices")
    return indices // 3


def validate(pack: Path) -> dict:
    """Check all required content against pins and the prepared-file manifest.

    Integrity and source compatibility only, not code signing, a visual verdict
    or match qualification. This expensive check is CLI-only, never a GUI tick.
    """
    pack = pack.absolute()
    if pack.is_symlink() or is_junction(pack):
        raise ValueError("Pack root cannot be a link/junction")
    pins = sources()
    record = read_json(confined(pack, MANIFEST))
    if record.get("schema") != 1 or record.get("reference_commit") != pins["reference_commit"]:
        raise ValueError("Pack schema/source revision differs; rebuild using this checkout")
    if (record.get("adapter_sha256") != adapter_digest()
            and record.get("adapter_canonical_sha256") != adapter_canonical_digest()):
        raise ValueError("Pack adapter differs from this client source; rebuild the pack")
    files = record.get("files")
    if not isinstance(files, dict) or not files or len(files) > 2048:
        raise ValueError("Missing/oversized prepared-file table")
    for name, sha in files.items():
        confined(pack, name)
        if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha):
            raise ValueError("Invalid prepared-file SHA256")
    if snapshot(pack) != files:
        raise ValueError("Pack files differ: missing, extra, linked or modified content")
    if not {"licenses/EndfieldHUD-LICENSE", "licenses/EndfieldHUD-CREDITS.md", "licenses/SOURCES.json"} <= files.keys():
        raise ValueError("Missing source/license notices")

    runtime = pack / "native_source_runtime"
    imported = read_json(runtime / "SOURCE_RUNTIME_MANIFEST.json")
    lock = read_json(Path(__file__).with_name("endfield_runtime_files.json"))
    if (imported.get("package_sha256") != pins["package_sha256"]
            or imported.get("engine_commit") != pins["reference_commit"]
            or imported.get("files") != lock["files"] or lock["package_sha256"] != pins["package_sha256"]):
        raise ValueError("Original resource package/source identity differs")
    for name, sha in lock["files"].items():
        if files.get("native_source_runtime/" + name) != sha:
            raise ValueError(f"Original resource changed: {name}")

    current = read_json(pack / "native_renderer/CURRENT.json")
    version = current.get("directory")
    if not isinstance(version, str) or not re.fullmatch(r"build_[0-9a-f]{16}", version):
        raise ValueError("Invalid renderer CURRENT directory")
    base = f"native_renderer/{version}/"
    build = read_json(confined(pack, base + "BUILD_MANIFEST.json"))
    binary = files.get(base + "polar_endfield.dll")
    if (build.get("reference_commit") != pins["reference_commit"]
            or build.get("adapter_sha256") != record["adapter_sha256"]
            or version != "build_" + record["adapter_sha256"][:16]
            or binary != build.get("binary_sha256") or binary != current.get("binary_sha256")):
        raise ValueError("Renderer pointer/build/source/binary identity differs")
    if base + "EndfieldHUD-MIT-LICENSE.txt" not in files:
        raise ValueError("Missing renderer license")
    triangles = {}
    for mode, source in pins["models"].items():
        metadata = read_json(pack / "maps" / (mode + ".json"))
        mesh_name = f"maps/{mode}.efmesh"
        if (metadata.get("mode") != mode or metadata.get("season") != 2027
                or metadata.get("source_sha256") != source["sha256"]
                or metadata.get("camera_up_axis") != source["up"]
                or metadata.get("gpu_mesh") != mode + ".efmesh"
                or metadata.get("gpu_mesh_sha256") != files.get(mesh_name)
                or metadata.get("preview_sha256") != files.get(f"maps/{mode}.png")):
            raise ValueError(f"Official field identity/geometry differs: {mode}")
        triangles[mode] = validate_mesh(pack / mesh_name)
        if metadata.get("gpu_triangles") != triangles[mode]:
            raise ValueError(f"Field triangle count differs: {mode}")
    return {"status": "PACK_VALID", "files": len(files), "maps": triangles,
            "binary_sha256": binary, "optional_boot": (pack / "boot").is_dir(),
            "optional_audio": (pack / "audio").is_dir(), "native_render_verified": False,
            "limits": "Integrity/source check only; run python -m tools.endfield.verify_pack_runtime for owned D3D rendering. No hardware/match qualification."}
