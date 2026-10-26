"""Build original EndfieldHUD probes or the retained POLAR native menu DLL.

Requires a user-supplied checkout at the pinned revision and ziglang (or an
explicit C++20 compiler). No game files, dependencies or executables are
downloaded by this tool. For the complete runtime pack (including all three
fields), use tools/endfield/prepare_pack.py and docs/endfield/PACK_SETUP.md.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from tools.endfield.pack import ADAPTER_SOURCES, adapter_digest

PIN = "cdbd4f573c8fa3b9c2048fa3d1b089e69196537b"
SOURCES = ("core/data/json.cpp", "core/scene.cpp", "core/source_animation.cpp",
           "core/source_layout.cpp", "core/source_camera.cpp")
RENDER_SOURCES = (
    "core/data/file_io.cpp", "core/data/data_store.cpp", "core/shell_packet.cpp",
    "core/motion.cpp", "core/source_animation_binary.cpp", "core/watch_runtime_input.cpp",
    "core/source_button_motion.cpp", "core/source_watch_layout.cpp", "core/source_canvas.cpp",
    "core/source_image_geometry.cpp", "core/source_watch_frame.cpp", "core/source_selectable_color.cpp",
    "native/source_scene.cpp", "native/source_graphics.cpp",
    "native/watch_presentation.cpp",
)
SHELL_SOURCES = (
    "app/source_watch_session.cpp", "core/source_native_labels.cpp", "core/notes_font_names.cpp",
    "native/renderer.cpp", "native/layer_scene.cpp", "native/layer_group.cpp", "native/layer_raster.cpp",
    "native/layer_text_layout.cpp", "native/rich_text_paint.cpp", "native/font_resources.cpp",
    "native/layer_mask.cpp", "native/layer_image_source.cpp", "core/notes_rich_text.cpp", "core/text_input.cpp",
    "native/watch_content.cpp",
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--cxx", type=Path)
    parser.add_argument("--native-render", action="store_true", help="Build the original D3D material probe (Windows only)")
    parser.add_argument("--native-shell", action="store_true", help="Include original native captions/icons in the diagnostic")
    parser.add_argument("--native-library", action="store_true", help="Build the retained client DirectComposition adapter")
    parser.add_argument("--install-root", type=Path, help="Publish the built adapter under a fresh versioned private renderer root")
    args = parser.parse_args()
    args.native_shell = args.native_shell or args.native_library
    args.native_render = args.native_render or args.native_shell
    reference, out = args.reference.resolve(), args.out.resolve()
    revision = subprocess.check_output(["git", "-C", str(reference), "rev-parse", "HEAD"], text=True).strip()
    if revision != PIN:
        raise SystemExit("Reference revision differs; do not silently change visual authority")
    subprocess.run(["git", "-C", str(reference), "diff", "--exit-code", "HEAD", "--", "windows"], check=True)
    out.mkdir(parents=True, exist_ok=True)
    host = "endfield_shell_probe" if args.native_shell else "endfield_native_probe" if args.native_render else "endfield_source_pose"
    binary = out / ("polar_endfield.dll" if args.native_library else host + ".exe")
    if binary.exists():
        raise SystemExit("Choose a new output directory; existing evidence is immutable")
    compiler = [str(args.cxx.resolve())] if args.cxx else [sys.executable, "-m", "ziglang", "c++"]
    compiler_version = subprocess.check_output([*compiler, "--version"], text=True)
    source_root = reference / "windows"
    workspace = Path(__file__).resolve().parents[2]
    relative = lambda path: os.path.relpath(path, workspace).replace("\\", "/")
    objects = []
    upstream = SOURCES + (RENDER_SOURCES if args.native_render else ()) + (SHELL_SOURCES if args.native_shell else ())
    if args.native_library:
        upstream += ("core/source_desktop_chrome.cpp", "native/chrome_presentation.cpp")
    adapter = Path(__file__).resolve().parents[2] / "native/endfield/bridge.cpp" if args.native_library else Path(__file__).with_name(host + ".cpp")
    sources = [adapter,
               *(source_root / name for name in upstream)]
    derived = {}
    if args.native_library:
        # Explicit DXGI adapter selection only; the pinned checkout and every
        # material/shader/animation remain untouched. Fail if the seam changed.
        original = source_root / 'native/renderer.cpp'
        code = original.read_bytes()
        marker = b'checked(D3D11CreateDevice(nullptr, '
        if code.count(marker) != 1:
            raise SystemExit('Pinned D3D device creation seam changed')
        selected = b'#include "native/endfield/adapter_selection.hpp"\n' + code.replace(
            marker, b'checked(polar::graphics::createDevice(nullptr, ')
        generated = out / 'client_adapter_renderer.cpp'
        generated.write_bytes(selected)
        sources[sources.index(original)] = generated
        derived[generated.name] = {'source':'native/renderer.cpp',
            'operation':'DXGI device selection and actual adapter diagnostics; unchanged rendering',
            'sha256':hashlib.sha256(selected).hexdigest()}
        # The source renderer already bounds targets to 8192 and 16M pixels,
        # but its scene-submission guard still rejects >4096 physical pixels.
        # At 200% DPI, a 2560 logical viewport is 5120 physical pixels. Port
        # only this guard, retaining every material/geometry/curve byte and the
        # renderer's allocation limit. The pinned checkout stays untouched.
        original = source_root / "native/source_scene.cpp"
        code = original.read_bytes()
        guard = b"p.width<=4096&&p.height<=4096"
        if code.count(guard) != 1:
            raise SystemExit("Pinned physical viewport guard changed")
        widened = code.replace(guard, b"p.width<=8192&&p.height<=8192")
        generated = out / "physical_viewport_source_scene.cpp"
        generated.write_bytes(widened)
        sources[sources.index(original)] = generated
        derived[generated.name] = {"source": "native/source_scene.cpp",
                                   "operation": "physical viewport bounds 4096 -> 8192; unchanged total pixel limit",
                                   "sha256": hashlib.sha256(widened).hexdigest()}
        original = source_root / "native/watch_content.cpp"
        code = original.read_bytes()
        marker = b"    for(const auto&entry:entries_){bool caption=false,icon=false;"
        if code.count(marker) != 1:
            raise SystemExit("Pinned navigation catalog extension boundary changed")
        extension = (workspace / "native/endfield/catalog_extension.inc").read_bytes()
        generated = out / "client_watch_content.cpp"
        extended = code.replace(marker,extension+b"\n"+marker)
        generated.write_bytes(extended)
        sources[sources.index(original)] = generated
        derived[generated.name] = {"source":"native/watch_content.cpp",
            "operation":"append bounded client entries using existing local caption/icon styles",
            "extension_sha256":hashlib.sha256(extension).hexdigest(),
            "sha256":hashlib.sha256(extended).hexdigest()}
    if args.native_render and not args.native_shell:
        # Original material and unrelated native-label adapters share one TU.
        # Compile the byte-identical material methods only; linking labels here
        # would install desktop font/providers in a material-only diagnostic.
        original = source_root / "native/watch_presentation.cpp"
        code = original.read_bytes()
        marker = b"WatchLabelPresentation::WatchLabelPresentation("
        if code.count(marker) != 1:
            raise SystemExit("Pinned source material boundary is ambiguous")
        subset = code.split(marker)[0] + b"\n} // namespace endfield::native\n"
        generated = out / "original_material_presentation.cpp"
        generated.write_bytes(subset)
        sources[sources.index(original)] = generated
        derived["original_material_presentation.cpp"] = {
            "source": "native/watch_presentation.cpp", "operation": "unchanged prefix before native-label adapter",
            "sha256": hashlib.sha256(subset).hexdigest(),
        }
    # Serial compilation also bounds peak memory and avoids the Zig 0.13
    # Windows driver's multi-translation-unit failure (silent exit 5).
    for index, source in enumerate(sources):
        obj = out / f"part_{index}.o"
        print(f"Compiling {source.name}", flush=True)
        compatibility = ["-DD3D11_FILTER_REDUCTION_TYPE_STANDARD=0"] if args.native_render and not args.cxx else []
        # MinGW names the standard (zero) D3D sampler reduction differently;
        # this is the Windows SDK enum value, not a material/filter substitution.
        optimize = ["-flto=thin"] if args.native_render else []
        local_includes = ["-I", relative(source_root / "native")] if source.name in ("physical_viewport_source_scene.cpp",'client_adapter_renderer.cpp') else []
        command = [*compiler, "-std=c++20", "-O2", *optimize, "-ffunction-sections", "-fdata-sections",
                        "-DNOMINMAX", "-DUNICODE", "-D_UNICODE", "-D_WIN32_WINNT=0x0a00", *compatibility, "-I", relative(source_root),
                        *local_includes, '-I', '.', "-c", relative(source), "-o", relative(obj)]
        # Local headers affect bridge AND the generated renderer translation
        # unit. A cached object may not survive a selection-policy/header edit.
        dependencies = adapter_digest() if args.native_library else ''
        signature = hashlib.sha256(source.read_bytes() + json.dumps([PIN, command, compiler_version, dependencies]).encode()).hexdigest()
        sidecar = obj.with_suffix(".signature")
        if not (obj.is_file() and sidecar.is_file() and sidecar.read_text() == signature):
            subprocess.run(command, cwd=workspace, check=True, timeout=180)
            sidecar.write_text(signature)
        objects.append(relative(obj))
    libraries = ["-ld3d11", "-ldxgi", "-ld3dcompiler_47", "-lbcrypt", "-lole32", "-ladvapi32"] if args.native_render else []
    if args.native_shell:
        libraries += ["-ldcomp", "-ld2d1", "-ldwrite", "-lwindowscodecs", "-luuid", "-luser32", "-lshlwapi"]
    subprocess.run([*compiler, *optimize, *(["-shared"] if args.native_library else []), *objects, *libraries, "-o", relative(binary)], cwd=workspace, check=True, timeout=300)
    shutil.copyfile(reference / "LICENSE", out / "EndfieldHUD-MIT-LICENSE.txt")
    manifest = {"scope": ("retained source renderer with RM content adapter" if args.native_library else
                          "original shell diagnostic; excludes RM map/client integration" if args.native_shell else
                          "original D3D material probe; excludes client integration and native labels" if args.native_render
                          else "source camera/curve oracle only; no UI equivalence verdict"),
                "reference_repository": "https://github.com/DDDuoDuo/EndfieldHUD", "reference_commit": PIN,
                "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
                "adapter_sha256": adapter_digest() if args.native_library else hashlib.sha256(adapter.read_bytes()).hexdigest(),
                "adapter_sources": {name:hashlib.sha256((workspace/name).read_bytes()).hexdigest()
                                    for name in ADAPTER_SOURCES} if args.native_library else {},
                "compiler": "\n".join(line for line in compiler_version.strip().splitlines()
                                      if not line.startswith("InstalledDir:")),
                "derived_translation_units": derived,
                "upstream_sources": {name: hashlib.sha256((source_root / name).read_bytes()).hexdigest() for name in upstream}}
    (out / "BUILD_MANIFEST.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    if args.install_root:
        if not args.native_library:
            raise SystemExit("Only a retained client adapter can be installed")
        install = args.install_root.resolve()
        install.mkdir(parents=True, exist_ok=True)
        version = "build_" + manifest["adapter_sha256"][:16]
        destination = install / version
        destination.mkdir(exist_ok=False)
        for name in (binary.name, "BUILD_MANIFEST.json", "EndfieldHUD-MIT-LICENSE.txt"):
            shutil.copyfile(out / name, destination / name)
        active = install / "CURRENT.json.tmp"
        active.write_text(json.dumps({"directory": version, "binary_sha256": manifest["binary_sha256"]}), encoding="utf-8")
        os.replace(active, install / "CURRENT.json")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
