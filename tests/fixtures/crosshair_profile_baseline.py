"""Data-only crosshair import and an explicitly static Qt approximation.

Protocol/layout/ConVar reference, accessed 2026-10-14:
https://github.com/akiver/csgo-sharecode/blob/main/doc/code_layout.md
Copyright 2017-present AkiVer, MIT. Independently implemented from the format,
not copied library source. Local Valve crosshair.vcss_c defines the preview
container, NOT engine geometry; a pixel-exact claim would be unsupported.
No CS2 access, console execution, telemetry synthesis or paint-time I/O.
"""
from __future__ import annotations

import json
import math
import re

ALPHABET = "ABCDEFGHJKLMNOPQRSTUVWXYZabcdefhijkmnopqrstuvwxyz23456789"
MAX_BYTES = 64 * 1024
PALETTE = ((255, 0, 0), (0, 255, 0), (255, 255, 0), (0, 0, 255), (0, 255, 255))
FIELDS = {
    "Length": (0, 255), "Gap": (-3840, 3840), "Thickness": (0, 32),
    "Red": (0, 255), "Green": (0, 255), "Blue": (0, 255), "Alpha": (0, 255),
    "Outline Red": (0, 255), "Outline Green": (0, 255), "Outline Blue": (0, 255), "Outline Alpha": (0, 255),
    "Outline Width": (0, 127.5), "Outline Mode": (0, 2), "CS2 Style": (0, 9),
    "Reference Height": (240, 65535), "Color Mode": (0, 5), "Dynamic Spread Limit": (0, 255),
    "Split Distance": (0, 127), "Inner Split Alpha": (0, 1), "Outer Split Alpha": (0, 1),
    "Split Size Ratio": (0, 1), "Fixed Gap": (-12.8, 12.7), "Scope Dot Scale": (0.1, 2),
}
INTEGERS = frozenset({"Red", "Green", "Blue", "Alpha", "Outline Red", "Outline Green", "Outline Blue",
                      "Outline Alpha", "Outline Mode", "CS2 Style", "Reference Height", "Color Mode"})
BOOLS = frozenset({"Center Dot", "T Shape", "Use Alpha", "Follow Recoil", "Weapon Gap", "Scope Dot Uses Color"})
FORMATS = ("Custom", "legacy-v1", "legacy-v3", "legacy-v4", "cs2-v1")
DISPLAY_SCALE_RANGE = (.5, 8.0)
# Local CS-only presentation baseline; never encoded into imported/exported data.
CS2_PRESENTATION_SCALE = 2.0
DEFAULTS = {
    "Length": 5.0, "Gap": 3.0, "Thickness": 1.0, "Red": 80, "Green": 255, "Blue": 80, "Alpha": 255,
    "Outline Red": 0, "Outline Green": 0, "Outline Blue": 0, "Outline Alpha": 255,
    "Outline Width": 1.0, "Outline Mode": 1, "CS2 Style": 4, "Reference Height": 1080,
    "Color Mode": 5, "Use Alpha": True, "Center Dot": False, "T Shape": False, "Follow Recoil": False,
    "Dynamic Spread Limit": 0, "Split Distance": 7, "Inner Split Alpha": 1.0, "Outer Split Alpha": 0.5,
    "Split Size Ratio": 0.3, "Fixed Gap": 3.0, "Weapon Gap": False,
    "Scope Dot Scale": 1.0, "Scope Dot Uses Color": False,
    "Geometry Units": "Pixels", "Source Format": "Custom", "Imported Code": "", "Import Notes": "",
}
CVARS = {
    "cl_crosshairsize": "Length", "cl_crosshair_length": "Length", "cl_crosshairgap": "Gap", "cl_crosshair_gap": "Gap",
    "cl_crosshairthickness": "Thickness", "cl_crosshair_thickness": "Thickness",
    "cl_crosshaircolor_r": "Red", "cl_crosshaircolor_g": "Green", "cl_crosshaircolor_b": "Blue",
    "cl_crosshairalpha": "Alpha", "cl_crosshaircolor_a": "Alpha",
    "cl_crosshairoutline_r": "Outline Red", "cl_crosshairoutline_g": "Outline Green",
    "cl_crosshairoutline_b": "Outline Blue", "cl_crosshairoutline_a": "Outline Alpha",
    "cl_crosshair_outlinethickness": "Outline Width", "cl_crosshair_drawoutline": "Outline Mode",
    "cl_crosshairstyle": "CS2 Style", "cl_crosshairdot": "Center Dot", "cl_crosshair_t": "T Shape",
    "cl_crosshaircolor": "Color Mode", "cl_crosshairusealpha": "Use Alpha", "cl_crosshair_recoil": "Follow Recoil",
    "cl_crosshairgap_useweaponvalue": "Weapon Gap", "cl_fixedcrosshairgap": "Fixed Gap",
    "cl_crosshair_screen_height": "Reference Height", "cl_crosshair_dynamic_spread_limit": "Dynamic Spread Limit",
    "cl_crosshair_dynamic_splitdist": "Split Distance", "cl_crosshair_dynamic_splitalpha_innermod": "Inner Split Alpha",
    "cl_crosshair_dynamic_splitalpha_outermod": "Outer Split Alpha", "cl_crosshair_dynamic_maxdist_splitratio": "Split Size Ratio",
    "cl_ironsight_dot_scale": "Scope Dot Scale", "cl_ironsight_usecrosshaircolor": "Scope Dot Uses Color",
}
JSON_ALIASES = dict(zip(
    ("length", "gap", "thickness", "red", "green", "blue", "alpha", "outlineRed", "outlineGreen", "outlineBlue",
     "outlineAlpha", "outline", "outlineMode", "style", "screenHeight", "color", "alphaEnabled", "centerDotEnabled",
     "tStyleEnabled", "followRecoil", "dynamicSpreadLimit", "splitDistance", "innerSplitAlpha", "outerSplitAlpha",
     "splitSizeRatio", "fixedCrosshairGap", "deployedWeaponGapEnabled", "scopeDotScale", "scopeDotUseCrosshairColor", "format"),
    ("Length", "Gap", "Thickness", "Red", "Green", "Blue", "Alpha", "Outline Red", "Outline Green", "Outline Blue",
     "Outline Alpha", "Outline Width", "Outline Mode", "CS2 Style", "Reference Height", "Color Mode", "Use Alpha", "Center Dot",
     "T Shape", "Follow Recoil", "Dynamic Spread Limit", "Split Distance", "Inner Split Alpha", "Outer Split Alpha",
     "Split Size Ratio", "Fixed Gap", "Weapon Gap", "Scope Dot Scale", "Scope Dot Uses Color", "Source Format")))


def validate_profile(values):
    """Validate the WHOLE profile before changing settings; no fractional enums."""
    if not isinstance(values, dict) or set(values) - set(DEFAULTS):
        raise ValueError("包含未知准星字段")
    result = dict(DEFAULTS)
    for key, value in values.items():
        if key in BOOLS:
            if type(value) not in (bool, int, float) or value not in (0, 1):
                raise ValueError(f"{key} 必须为 true/false 或 0/1")
            result[key] = bool(value)
        elif key in FIELDS:
            try:
                finite = type(value) in (int, float) and math.isfinite(value)
            except OverflowError:
                finite = False
            if not finite:
                raise ValueError(f"{key} 必须为有限数值")
            low, high = FIELDS[key]
            if not low <= value <= high or (key in INTEGERS and int(value) != value):
                raise ValueError(f"{key} 超出有效范围 {low}–{high} 或不是整数")
            result[key] = int(value) if key in INTEGERS else value
        elif key == "Geometry Units":
            if value not in ("Pixels", "Legacy"):
                raise ValueError("未知准星尺寸单位")
            result[key] = value
        elif key == "Source Format":
            if value not in FORMATS:
                raise ValueError("暂不支持此准星版本")
            result[key] = value
        else:
            if not isinstance(value, str) or len(value) > (64 if key == "Imported Code" else 4096):
                raise ValueError(f"{key} 无效或过长")
            result[key] = value
    fmt = result["Source Format"]
    if result["CS2 Style"] > {"legacy-v1": 5, "legacy-v3": 7, "legacy-v4": 8}.get(fmt, 9):
        raise ValueError(f"{fmt} 不支持此样式编号")
    if fmt != "Custom" and result["Geometry Units"] != ("Legacy" if fmt == "legacy-v1" else "Pixels"):
        raise ValueError("版本与尺寸单位冲突")
    return result


def _share_bytes(code):
    if not isinstance(code, str):
        raise ValueError("分享码必须是文本")
    code = code.strip()
    chars = re.escape(ALPHABET)
    if re.fullmatch(r"CS[" + chars + r"]{44}", code):
        payload, size, container = code[2:], 32, "cs2"
    elif re.fullmatch(r"CSGO(?:-[" + chars + r"]{5}){5}", code):
        payload, size, container = code[5:].replace("-", ""), 18, "legacy"
    elif re.fullmatch(r"CSGO[" + chars + r"]{25}", code):
        payload, size, container = code[4:], 18, "legacy"
    else:
        raise ValueError("不是有效的 CS / CSGO 准星分享码")
    number = 0
    for character in reversed(payload):
        number = number * 57 + ALPHABET.index(character)
    try:
        data = number.to_bytes(size, "big")
    except OverflowError:
        raise ValueError("准星分享码整数超出容器") from None
    if data[0] != sum(data[1:]) % 256:
        raise ValueError("准星分享码校验失败")
    return code, data, container


def decode_share_code(code):
    code, b, container = _share_bytes(code)
    fmt = ("cs2-v" if container == "cs2" else "legacy-v") + str(b[1])
    if fmt not in FORMATS:
        raise ValueError("暂不支持此准星分享码版本（无 legacy-v2）")
    v = dict(DEFAULTS, **{"Source Format": fmt, "Imported Code": code})
    notes = []
    if fmt == "legacy-v1":
        signed = lambda n: n if n < 128 else n - 256
        v.update({"Geometry Units": "Legacy", "Reference Height": 480, "Gap": signed(b[2]) / 10,
                  "Outline Width": b[3] / 2, "Red": b[4], "Green": b[5], "Blue": b[6], "Alpha": b[7],
                  "Split Distance": b[8] & 7, "Follow Recoil": bool(b[8] & 128), "Fixed Gap": signed(b[9]) / 10,
                  "Color Mode": b[10] & 7, "Outline Mode": int(bool(b[10] & 8)), "Inner Split Alpha": (b[10] >> 4) / 10,
                  "Outer Split Alpha": (b[11] & 15) / 10, "Split Size Ratio": (b[11] >> 4) / 10,
                  "Thickness": b[12] / 10, "CS2 Style": (b[13] >> 1) & 7, "Center Dot": bool(b[13] & 16),
                  "Weapon Gap": bool(b[13] & 32), "Use Alpha": bool(b[13] & 64), "T Shape": bool(b[13] & 128), "Length": b[14] / 10})
        reserved = bool(b[8] & 0x78 or b[13] & 1 or any(b[15:]))
    elif fmt in ("legacy-v3", "legacy-v4"):
        packed = int.from_bytes(b[10:14], "little")
        v.update({"Reference Height": int.from_bytes(b[14:16], "little"), "CS2 Style": b[2] & 15,
                  "Follow Recoil": bool(b[2] & 16), "Center Dot": bool(b[2] & 64), "T Shape": bool(b[2] & 128),
                  "Red": b[3], "Green": b[4], "Blue": b[5], "Alpha": b[6], "Gap": b[7], "Length": b[8],
                  "Dynamic Spread Limit": b[9], "Split Distance": packed & 127,
                  "Inner Split Alpha": ((packed >> 7) & 31) / 20, "Outer Split Alpha": (((packed >> 12) & 15) + 6) / 20,
                  "Split Size Ratio": ((packed >> 16) & 127) / 100, "Thickness": (packed >> 23) & 31,
                  "Outline Mode": int(bool(b[2] & 32)) if fmt == "legacy-v3" else (packed >> 28) & 3})
        reserved = bool(any(b[16:]) or packed >> (28 if fmt == "legacy-v3" else 30)
                        or (fmt == "legacy-v4" and b[2] & 32))
    else:
        packed = int.from_bytes(b[18:22], "little")
        v.update({"Reference Height": int.from_bytes(b[2:4], "little"), "CS2 Style": b[4] & 31,
                  "Follow Recoil": bool(b[4] & 32), "Center Dot": bool(b[4] & 64), "T Shape": bool(b[4] & 128),
                  "Red": b[5], "Green": b[6], "Blue": b[7], "Alpha": b[8], "Outline Red": b[9],
                  "Outline Green": b[10], "Outline Blue": b[11], "Outline Alpha": b[12], "Thickness": b[13] & 63,
                  "Outline Mode": b[13] >> 6, "Gap": int.from_bytes(b[14:16], "little", signed=True), "Length": b[16],
                  "Dynamic Spread Limit": b[17], "Split Distance": packed & 127,
                  "Inner Split Alpha": ((packed >> 7) & 127) / 100, "Outer Split Alpha": (((packed >> 14) & 127) + 30) / 100,
                  "Split Size Ratio": ((packed >> 21) & 127) / 100, "Scope Dot Uses Color": bool(packed & (1 << 28)),
                  "Scope Dot Scale": (b[22] + 10) / 100})
        # Mirror documented CS2 sanitation, WITH explicit notes and original code.
        if v["Reference Height"] == 0:
            raise ValueError("分享码参考高度不能为0")
        for key in ("Reference Height", "CS2 Style", "Thickness", "Outline Mode", "Gap",
                    "Inner Split Alpha", "Outer Split Alpha", "Split Size Ratio", "Scope Dot Scale"):
            lo, hi = FIELDS[key]
            adjusted = min(hi, max(lo, v[key]))
            if adjusted != v[key]:
                notes.append(f"{key}: {v[key]} → {adjusted}（CS2规范限幅；原始码保留）")
                v[key] = adjusted
        reserved = bool(packed >> 29 or any(b[23:]))
    if reserved:
        notes.append("分享码包含非零保留位：不解释这些未知位，原始分享码完整保留。")
    v["Import Notes"] = "\n".join(notes)
    return validate_profile(v)


def _canonical_json(data):
    if not isinstance(data, dict) or not data:
        raise ValueError("JSON必须是非空准星对象")
    v = {}
    for key, value in data.items():
        if key == "outlineEnabled":
            if type(value) not in (bool, int, float) or value not in (0, 1):
                raise ValueError("outlineEnabled 必须为开关")
            name, value = "Outline Mode", int(value)
        else:
            name = JSON_ALIASES.get(key, key)
        if name in v:
            raise ValueError(f"重复/冲突准星字段：{name}")
        v[name] = value
    if v.get("Source Format") == "legacy-v1":
        v.setdefault("Geometry Units", "Legacy")
        v.setdefault("Reference Height", 480)
    return validate_profile(v)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"JSON重复字段：{key}")
        result[key] = value
    return result


def _parse_cfg(text):
    # Strip comments BEFORE splitting commands; semicolons in comments stay there.
    commands = {}
    numeric = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
    pattern = re.compile(r'(?P<key>[a-z_]+|"[a-z_]+")\s+(?P<num>' + numeric + r'|"' + numeric + r'")')
    for line in text.splitlines():
        for statement in line.split("//", 1)[0].split(";"):
            statement = statement.strip()
            if not statement:
                continue
            match = pattern.fullmatch(statement)
            if match is None or match["key"].strip('"') not in CVARS:
                raise ValueError("只允许准星白名单参数，不执行CFG命令；未知字段不会忽略")
            commands[match["key"].strip('"')] = float(match["num"].strip('"'))
    if not commands:
        raise ValueError("没有找到准星参数")
    legacy = any(k in commands for k in ("cl_crosshairsize", "cl_crosshairgap", "cl_crosshairthickness",
                                         "cl_crosshair_outlinethickness", "cl_fixedcrosshairgap", "cl_crosshairgap_useweaponvalue"))
    modern = any(k in commands for k in ("cl_crosshair_length", "cl_crosshair_gap", "cl_crosshair_thickness",
                                         "cl_crosshair_screen_height", "cl_crosshaircolor_a", "cl_crosshairoutline_a"))
    if legacy and modern:
        raise ValueError("CFG混合新旧尺寸/透明度协议，请分开导入以免误换算")
    v = {"Geometry Units": "Legacy" if legacy else "Pixels"}
    if legacy:
        v.update({"Reference Height": 480, "Source Format": "legacy-v1"})
    header = re.search(r"(?m)^// RM crosshair format: (legacy-v[134]|cs2-v1)$", text)
    if header:
        v["Source Format"] = header[1]
    original = re.search(r"(?m)^// RM original code: (\S+)$", text)
    if original:
        # Validate data embedded in our own CFG metadata as strictly as text.
        decoded = decode_share_code(original[1])
        v["Imported Code"] = original[1]
        v["Import Notes"] = decoded["Import Notes"]
    for key, value in commands.items():
        name = CVARS[key]
        if name in v and v[name] != value:
            raise ValueError(f"冲突准星字段：{name}")
        v[name] = value
    return validate_profile(v)


def parse_external(text):
    if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_BYTES:
        raise ValueError("准星配置必须为文本且不超过64KiB")
    text = text.lstrip("\ufeff").strip()
    if text.startswith("CS"):
        return decode_share_code(text)
    if text.startswith(("{", "[")):
        try:
            return _canonical_json(json.loads(text, object_pairs_hook=_unique_object))
        except (json.JSONDecodeError, RecursionError) as exc:
            raise ValueError("无效准星JSON") from exc
    return _parse_cfg(text)


def profile_from_module(module):
    if module is None:
        raise ValueError("准星模块不可用")
    return validate_profile({key: s.value for key in DEFAULTS if (s := module.get_setting(key)) is not None})


def apply_profile(module, values):
    values = validate_profile(values)
    if module is None:
        raise ValueError("准星模块不可用")
    settings = {key: module.get_setting(key) for key in (*values, "Style")}
    if any(s is None for s in settings.values()):
        raise ValueError("准星模块缺少导入字段，未修改任何设置")
    for key, value in values.items():
        settings[key].value = value
    settings["Style"].value = "CS2"


def profile_notice(values):
    v = validate_profile(values)
    lines = ["这是CS准星的静态近似，不是CS2引擎1:1复现；原有RM准星和热量配色仍可独立选择。",
             "动态spread/shot feedback/recoil、武器gap、split参数及scope dot已保留，",
             "但RM无对应CS遥测/开镜状态，不会模拟这些效果；尺寸/圆环/quad/半描边也是Qt近似。"]
    if v["Geometry Units"] == "Legacy":
        lines.append("旧版CFG/CSGO尺寸按480px参考高度近似换算；旧样式0/1按经典十字显示。")
    if v["Import Notes"]:
        lines.append(v["Import Notes"])
    return "\n".join(lines)


def import_dialog(host, *, file=False):
    from pathlib import Path
    from PySide6.QtWidgets import QFileDialog, QInputDialog, QMessageBox
    try:
        if file:
            name, _ = QFileDialog.getOpenFileName(host, "导入准星配置", "", "准星配置 (*.json *.cfg *.txt)")
            if not name:
                return
            # Bound the read, not only an earlier stat vulnerable to TOCTOU.
            with Path(name).open("rb") as stream:
                payload = stream.read(MAX_BYTES + 1)
            if len(payload) > MAX_BYTES:
                raise ValueError("准星配置超过64KiB")
            text = payload.decode("utf-8-sig")
        else:
            text, ok = QInputDialog.getMultiLineText(host, "导入CS准星", "粘贴 CS / CSGO 分享码、准星JSON或白名单CFG：")
            if not ok:
                return
        values = parse_external(text)
        apply_profile(host.registry.get("Crosshair"), values)
        host.invalidate_transient_overlay_scene()
        host.invalidate_gui_cache()
        host.frame_scheduler.force()
        host.auto_save_config()
        host.toast("准星已导入（静态近似，动态/后坐力未模拟）")
        QMessageBox.information(host, "准星导入说明", profile_notice(values))
    except (OSError, UnicodeError, ValueError, TypeError, KeyError) as exc:
        host.toast("准星导入失败：" + str(exc))


def show_import_details(host):
    from PySide6.QtWidgets import QMessageBox
    try:
        v = profile_from_module(host.registry.get("Crosshair"))
        details = "\n".join(f"{key}: {value}" for key, value in v.items() if key != "Import Notes")
        QMessageBox.information(host, "准星参数与限制", profile_notice(v) + "\n\n" + details)
    except ValueError as exc:
        host.toast("准星参数无效：" + str(exc))


def display_scale_from_module(module):
    """Local presentation preference, deliberately outside the CS wire profile."""
    setting = module.get_setting("Crosshair Scale") if module is not None else None
    value = setting.value if setting is not None else 1.0
    try:
        finite = type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        return 1.0
    return max(DISPLAY_SCALE_RANGE[0], min(DISPLAY_SCALE_RANGE[1], value))


def paint_crosshair(p, module, width, height, *, display_scale=None, preview_zoom=1.0):
    """Paint ten modern static signatures, without simulating gameplay input."""
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QPainter, QPainterPath
    try:
        v = profile_from_module(module)
    except (ValueError, TypeError):
        return False  # Invalid persisted data must not crash a combat frame.
    if width <= 0 or height <= 0:
        return False
    multiplier = display_scale_from_module(module) if display_scale is None else display_scale
    if type(multiplier) not in (int, float) or not DISPLAY_SCALE_RANGE[0] <= multiplier <= DISPLAY_SCALE_RANGE[1]:
        return False
    if type(preview_zoom) not in (int, float) or not 1 <= preview_zoom <= 8:
        return False
    scale = height / v["Reference Height"] * multiplier * CS2_PRESENTATION_SCALE
    length, gap, thick = (v[k] * scale for k in ("Length", "Gap", "Thickness"))
    # Aliased subpixel strips can disappear entirely at 720p or small local
    # scales. Keep a positive stroke at least one physical destination pixel,
    # accounting for HUD and device DPR. Preview inspection magnifies this
    # target-pixel floor together with the geometry, rather than thinning it.
    # An explicitly zero thickness remains invisible.
    if thick > 0:
        transform = p.deviceTransform()
        pixel_scale = min(math.hypot(transform.m11(), transform.m12()),
                          math.hypot(transform.m21(), transform.m22()))
        thick = max(thick, preview_zoom / max(0.001, pixel_scale))
    legacy = v["Geometry Units"] == "Legacy"
    if legacy:
        gap += 4 * scale  # Approximation; engine math is not in UI CSS.
    cx, cy = width / 2, height / 2
    style = 4 if legacy else int(v["CS2 Style"])
    main_path, border_path = QPainterPath(), QPainterPath()
    outline = v["Outline Width"] * scale * (0.5 if v["Outline Mode"] == 2 else 1) if v["Outline Mode"] else 0

    def rectangle(x, y, w, h):
        nonlocal main_path, border_path
        if w <= 0 or h <= 0:
            return
        rect = QRectF(x, y, w, h)
        shape = QPainterPath()
        shape.addRect(rect)
        main_path = main_path.united(shape)
        if outline:
            border = QPainterPath()
            border.addRect(rect.adjusted(-outline, -outline, outline, outline))
            border_path = border_path.united(border)

    if thick > 0 and style != 6:
        if style in (1, 3, 8):
            radius = max(0.0, gap + length)

            def ring_path(half, stroke):
                outer, inner = QPainterPath(), QPainterPath()
                outer_rect = QRectF(cx - half - stroke / 2, cy - half - stroke / 2, 2 * half + stroke, 2 * half + stroke)
                inner_radius = max(0.0, half - stroke / 2)
                inner_rect = QRectF(cx - inner_radius, cy - inner_radius, 2 * inner_radius, 2 * inner_radius)
                if style == 8:
                    outer.addRect(outer_rect)
                    inner.addRect(inner_rect)
                else:
                    outer.addEllipse(outer_rect)
                    inner.addEllipse(inner_rect)
                return outer.subtracted(inner)

            main_path = ring_path(radius, thick)
            if outline:
                border_path = ring_path(radius, thick + 2 * outline)
        elif style in (7, 9):
            # Quad is four separated L-shaped corner brackets, NOT square8.
            for sx in (-1, 1):
                for sy in (-1, 1):
                    if v["T Shape"] and sy == -1:
                        continue
                    x, y = cx + sx * gap, cy + sy * gap
                    rectangle(x - length if sx < 0 else x, y - thick / 2, length, thick)
                    rectangle(x - thick / 2, y - length if sy < 0 else y, thick, length)
        else:
            rectangle(cx - gap - length, cy - thick / 2, length, thick)
            rectangle(cx + gap, cy - thick / 2, length, thick)
            rectangle(cx - thick / 2, cy + gap, thick, length)
            if not v["T Shape"]:
                rectangle(cx - thick / 2, cy - gap - length, thick, length)
    if v["Center Dot"] or style == 6:
        rectangle(cx - thick / 2, cy - thick / 2, thick, thick)
    rgb = PALETTE[v["Color Mode"]] if v["Color Mode"] < 5 else tuple(v[k] for k in ("Red", "Green", "Blue"))
    alpha = v["Alpha"] if v["Use Alpha"] else 255
    main = QColor(*rgb, alpha)
    border = QColor(*(v[k] for k in ("Outline Red", "Outline Green", "Outline Blue")), alpha if legacy else v["Outline Alpha"])
    p.save()
    try:
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        p.setPen(Qt.PenStyle.NoPen)
        if outline:
            p.fillPath(border_path.subtracted(main_path), border)
        p.fillPath(main_path, main)
    finally:
        p.restore()
    return True


def _wire_integer(v, key, multiplier=1, offset=0, maximum=255, minimum=0):
    raw = (v[key] - offset) * multiplier
    integer = round(raw)
    if not math.isclose(raw, integer, abs_tol=1e-7) or not minimum <= integer <= maximum:
        raise ValueError(f"{key}={v[key]} 无法按目标分享码精度/范围表达，未导出")
    return integer


def encode_share_code(values, format=None):
    """Encode all four formats; refuse lossy conversion instead of dropping data."""
    v = validate_profile(values)
    fmt = format or (v["Source Format"] if v["Source Format"] != "Custom" else "cs2-v1")
    if fmt not in FORMATS[1:]:
        raise ValueError("未知导出版本")
    legacy = fmt == "legacy-v1"
    if v["Geometry Units"] != ("Legacy" if legacy else "Pixels"):
        raise ValueError("目标版本尺寸单位不同；请在编辑器明确转换参数，不能静默换算")
    incompatible = []
    absent = []
    if fmt != "legacy-v1":
        absent += ["Color Mode", "Use Alpha", "Fixed Gap", "Weapon Gap", "Outline Width"]
    if fmt != "cs2-v1":
        absent += ["Outline Red", "Outline Green", "Outline Blue", "Outline Alpha", "Scope Dot Scale", "Scope Dot Uses Color"]
    if fmt == "legacy-v1":
        absent += ["Dynamic Spread Limit"]
        if v["Reference Height"] != 480:
            incompatible.append("Reference Height（旧版固定为480）")
    incompatible += [key for key in absent if v[key] != DEFAULTS[key]]
    if incompatible:
        raise ValueError("目标版本无法表达：" + ", ".join(incompatible) + "；请选原格式/完整JSON")
    maximum_style = {"legacy-v1": 5, "legacy-v3": 7, "legacy-v4": 8, "cs2-v1": 9}[fmt]
    if v["CS2 Style"] > maximum_style:
        raise ValueError("目标版本不支持当前样式")
    if fmt in ("legacy-v1", "legacy-v3") and v["Outline Mode"] > 1:
        raise ValueError("目标版本不支持半描边")
    # Preserve original unused bits and byte-for-byte round trips, including
    # bytes the current game normalizes. Any edited KNOWN field is re-encoded.
    original = None
    if v["Imported Code"]:
        try:
            _, original, container = _share_bytes(v["Imported Code"])
            decoded = decode_share_code(v["Imported Code"])
            if decoded["Source Format"] != fmt:
                original = None
            elif all(v[k] == decoded[k] for k in DEFAULTS if k not in ("Source Format", "Import Notes", "Imported Code")):
                return v["Imported Code"]
        except ValueError:
            original = None
    size = 32 if fmt == "cs2-v1" else 18
    b = bytearray(original) if original is not None else bytearray(size)
    b[1] = 1 if fmt in ("cs2-v1", "legacy-v1") else int(fmt[-1])
    if fmt == "legacy-v1":
        b[2] = _wire_integer(v, "Gap", 10, maximum=127, minimum=-128) & 255
        b[3] = _wire_integer(v, "Outline Width", 2)
        b[4:8] = bytes(v[k] for k in ("Red", "Green", "Blue", "Alpha"))
        b[8] = (b[8] & 0x78) | _wire_integer(v, "Split Distance", maximum=7) | (int(v["Follow Recoil"]) << 7)
        b[9] = _wire_integer(v, "Fixed Gap", 10, maximum=127, minimum=-128) & 255
        b[10] = v["Color Mode"] | (v["Outline Mode"] << 3) | (_wire_integer(v, "Inner Split Alpha", 10, maximum=10) << 4)
        b[11] = _wire_integer(v, "Outer Split Alpha", 10, maximum=10) | (_wire_integer(v, "Split Size Ratio", 10, maximum=10) << 4)
        b[12] = _wire_integer(v, "Thickness", 10)
        b[13] = (b[13] & 1) | (v["CS2 Style"] << 1) | (int(v["Center Dot"]) << 4) | (int(v["Weapon Gap"]) << 5) | (int(v["Use Alpha"]) << 6) | (int(v["T Shape"]) << 7)
        b[14] = _wire_integer(v, "Length", 10)
    elif fmt in ("legacy-v3", "legacy-v4"):
        b[2] = (b[2] & (32 if fmt == "legacy-v4" else 0)) | v["CS2 Style"] | (int(v["Follow Recoil"]) << 4) | (int(v["Center Dot"]) << 6) | (int(v["T Shape"]) << 7)
        if fmt == "legacy-v3":
            b[2] |= v["Outline Mode"] << 5
        b[3:7] = bytes(v[k] for k in ("Red", "Green", "Blue", "Alpha"))
        b[7:10] = bytes(_wire_integer(v, k) for k in ("Gap", "Length", "Dynamic Spread Limit"))
        packed = int.from_bytes(b[10:14], "little") & (0xf0000000 if fmt == "legacy-v3" else 0xc0000000)
        packed |= _wire_integer(v, "Split Distance", maximum=127)
        packed |= _wire_integer(v, "Inner Split Alpha", 20, maximum=20) << 7
        packed |= _wire_integer(v, "Outer Split Alpha", 20, .3, maximum=14) << 12
        packed |= _wire_integer(v, "Split Size Ratio", 100, maximum=100) << 16
        packed |= _wire_integer(v, "Thickness", maximum=31) << 23
        if fmt == "legacy-v4":
            packed |= v["Outline Mode"] << 28
        b[10:14] = packed.to_bytes(4, "little")
        b[14:16] = v["Reference Height"].to_bytes(2, "little")
    else:
        b[2:4] = v["Reference Height"].to_bytes(2, "little")
        b[4] = v["CS2 Style"] | (int(v["Follow Recoil"]) << 5) | (int(v["Center Dot"]) << 6) | (int(v["T Shape"]) << 7)
        b[5:13] = bytes(v[k] for k in ("Red", "Green", "Blue", "Alpha", "Outline Red", "Outline Green", "Outline Blue", "Outline Alpha"))
        b[13] = _wire_integer(v, "Thickness", maximum=32) | (v["Outline Mode"] << 6)
        b[14:16] = _wire_integer(v, "Gap", maximum=3840, minimum=-3840).to_bytes(2, "little", signed=True)
        b[16:18] = bytes(_wire_integer(v, k) for k in ("Length", "Dynamic Spread Limit"))
        packed = int.from_bytes(b[18:22], "little") & 0xe0000000
        packed |= _wire_integer(v, "Split Distance", maximum=127)
        packed |= _wire_integer(v, "Inner Split Alpha", 100, maximum=100) << 7
        packed |= _wire_integer(v, "Outer Split Alpha", 100, .3, maximum=70) << 14
        packed |= _wire_integer(v, "Split Size Ratio", 100, maximum=100) << 21
        packed |= int(v["Scope Dot Uses Color"]) << 28
        b[18:22] = packed.to_bytes(4, "little")
        b[22] = _wire_integer(v, "Scope Dot Scale", 100, .1, maximum=190)
    b[0] = sum(b[1:]) % 256
    integer, chars = int.from_bytes(b, "big"), []
    for _ in range(44 if size == 32 else 25):
        integer, remainder = divmod(integer, 57)
        chars.append(ALPHABET[remainder])
    if integer:
        raise ValueError("分享码整数超出文本容器")
    text = "".join(chars)
    return "CS" + text if size == 32 else "CSGO-" + "-".join(text[i:i + 5] for i in range(0, 25, 5))


def export_json(values):
    return json.dumps(validate_profile(values), ensure_ascii=False, indent=2) + "\n"


def export_cfg(values, format=None):
    v = validate_profile(values)
    fmt = format or (v["Source Format"] if v["Source Format"] != "Custom" else "cs2-v1")
    encode_share_code(v, fmt)  # Same explicit representability checks as codes.
    common = ["cl_crosshair_drawoutline", "cl_crosshairstyle", "cl_crosshairdot", "cl_crosshair_t", "cl_crosshair_recoil",
              "cl_crosshaircolor_r", "cl_crosshaircolor_g", "cl_crosshaircolor_b", "cl_crosshair_dynamic_splitdist",
              "cl_crosshair_dynamic_splitalpha_innermod", "cl_crosshair_dynamic_splitalpha_outermod", "cl_crosshair_dynamic_maxdist_splitratio"]
    if fmt == "legacy-v1":
        names = common + ["cl_crosshairsize", "cl_crosshairgap", "cl_crosshairthickness", "cl_crosshair_outlinethickness",
                          "cl_crosshairalpha", "cl_crosshaircolor", "cl_crosshairusealpha", "cl_fixedcrosshairgap", "cl_crosshairgap_useweaponvalue"]
    else:
        names = common + ["cl_crosshair_length", "cl_crosshair_gap", "cl_crosshair_thickness", "cl_crosshaircolor_a",
                          "cl_crosshair_screen_height", "cl_crosshair_dynamic_spread_limit"]
        if fmt == "cs2-v1":
            names += ["cl_crosshairoutline_r", "cl_crosshairoutline_g", "cl_crosshairoutline_b", "cl_crosshairoutline_a",
                      "cl_ironsight_dot_scale", "cl_ironsight_usecrosshaircolor"]
    def number(value):
        return str(int(value)) if isinstance(value, bool) else format_number(value)
    header = "// RM crosshair format: " + fmt + "\n"
    if v["Imported Code"]:
        header += "// RM original code: " + v["Imported Code"] + "\n"
    if v["Import Notes"]:
        header += "".join("// " + line + "\n" for line in v["Import Notes"].splitlines())
    return header + "\n".join(f'{name} "{number(v[CVARS[name]])}"' for name in names) + "\n"


def format_number(value):
    return f"{value:g}"


def _editor_font(assets, *, size=10, bold=False):
    """Own the font chain; never resolve a Minecraft/host font into this page.

    Qt's Windows offscreen plugin does not always enumerate installed system
    faces. Register the machine's YaHei collection when needed, not a download
    or a font copied from CS2. This happens on editor construction, not paint.
    """
    import os
    from pathlib import Path
    from PySide6.QtGui import QFont, QFontDatabase
    available = set(QFontDatabase.families())
    if os.name == 'nt' and not {'Microsoft YaHei UI', 'Microsoft YaHei'} & available:
        path = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts/msyh.ttc'
        if path.is_file():
            QFontDatabase.addApplicationFont(str(path))
    primary = assets.families.get('font_condensed' if bold else 'font_regular', 'Arial')
    font = QFont()
    font.setFamilies(list(dict.fromkeys((primary, 'Microsoft YaHei UI', 'Microsoft YaHei',
                                        'Noto Sans CJK SC', 'Arial', 'Sans Serif'))))
    font.setPointSizeF(size)
    font.setWeight(QFont.Weight.Bold if bold else QFont.Weight.Normal)
    font.setStyleName('')
    font.setItalic(False)
    font.setStretch(100)
    font.setKerning(True)
    font.setStyleHint(QFont.StyleHint.SansSerif)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
    return font


def _create_editor(host, *, embedded=False):
    """Native modal UI; owns draft values only, never modifies backend settings."""
    from pathlib import Path
    from types import SimpleNamespace
    from PySide6.QtCore import Qt, QRectF, QEvent
    from PySide6.QtGui import QColor, QPainter, QLinearGradient, QPalette
    from PySide6.QtWidgets import (
        QApplication, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog,
        QBoxLayout, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
        QPushButton, QScrollArea, QSizePolicy, QSpinBox, QVBoxLayout, QWidget,
    )
    from .ui.cs2_assets import local_cs2_assets
    if embedded:
        from .ui.jello_widgets import JelloCheckBox as QCheckBox
        from .ui.jello_widgets import JelloComboBox as QComboBox
    from .ui.jello_locale import chinese_enabled, setting_label
    chinese = chinese_enabled(host.registry)
    label = lambda value: setting_label(value, chinese)

    class Preview(QWidget):
        def __init__(self, editor, name, colors, grid=False):
            super().__init__(editor)
            self.editor, self.name, self.colors, self.grid = editor, name, colors, grid
            self.setMinimumSize(220, 120)
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        def paintEvent(self, event):
            painter = QPainter(self)
            try:
                painter.setClipRect(self.rect())
                painter.setFont(self.editor.font())
                painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
                gradient = QLinearGradient(0, 0, self.width(), self.height())
                gradient.setColorAt(0, QColor(self.colors[0]))
                gradient.setColorAt(1, QColor(self.colors[1]))
                painter.fillRect(self.rect(), gradient)
                if self.grid:
                    painter.setPen(QColor(255, 255, 255, 35))
                    for x in range(0, self.width(), 24):
                        painter.drawLine(x, 0, x, self.height())
                    for y in range(0, self.height(), 24):
                        painter.drawLine(0, y, self.width(), y)
                painter.setPen(QColor('#18232d') if self.name == 'LIGHT' else QColor('#eeeeee'))
                caption = self.name + ' · 合成背景 / SYNTHETIC'
                painter.drawText(QRectF(12, 8, self.width() - 24, 24), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                                 painter.fontMetrics().elidedText(caption, Qt.TextElideMode.ElideRight, self.width() - 24))
                v = self.editor.draft()
                # The selector names physical target pixels. Convert to this
                # preview widget's logical coordinates before Qt applies DPR.
                ref_height = int(self.editor.resolution.currentText()) / max(0.001, self.devicePixelRatioF())
                ref_width = round(ref_height * 16 / 9)
                zoom = self.editor.zoom.value()
                # Reserve the caption and clip before applying the virtual
                # resolution/zoom transform. Extreme imported geometry cannot
                # overwrite the label, padding or neighbouring previews.
                canvas = QRectF(8, 36, self.width() - 16, self.height() - 44)
                painter.setClipRect(canvas, Qt.ClipOperation.IntersectClip)
                painter.translate(canvas.center().x() - ref_width * zoom / 2, canvas.center().y() - ref_height * zoom / 2)
                painter.scale(zoom, zoom)
                module = SimpleNamespace(get_setting=lambda key: SimpleNamespace(value=v[key]))
                paint_crosshair(painter, module, ref_width, ref_height,
                                display_scale=self.editor.display_scale.value(), preview_zoom=zoom)
            except ValueError:
                pass  # Error label describes invalid drafts, never changes host.
            finally:
                painter.end()

    class Editor(QWidget if embedded else QDialog):
        def __init__(self):
            super().__init__(host)
            self.setObjectName('cs2_crosshair_editor')
            self.setWindowTitle('CS CROSSHAIR · 准星设置')
            if not embedded:
                self.setWindowModality(Qt.WindowModality.ApplicationModal)
                # Qt can suppress a parent ApplicationShortcut when a nested
                # modal input/file dialog is active. A scoped application event
                # filter covers those Qt child dialogs too. It is inert while
                # hidden and QObject destruction removes it automatically.
                # Embedded pages use the Settings Center's single F12 route.
                QApplication.instance().installEventFilter(self)
            self.setMinimumSize(300, 240)
            if embedded:
                self.resize(1140, 830)
            else:
                available = self.screen().availableGeometry()
                self.resize(min(1140, max(300, available.width() - 40)),
                            min(830, max(240, available.height() - 40)))
            assets = local_cs2_assets()
            base_font = _editor_font(assets)
            title_font = _editor_font(assets, size=24, bold=True)
            self.setFont(base_font)
            palette = QPalette()
            palette.setColor(QPalette.ColorRole.Window, QColor('#17232b'))
            palette.setColor(QPalette.ColorRole.WindowText, QColor('#edf0f2'))
            palette.setColor(QPalette.ColorRole.Text, QColor('#f5f6f7'))
            palette.setColor(QPalette.ColorRole.Base, QColor('#263641'))
            self.setPalette(palette)
            self.setAutoFillBackground(True)
            self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            regular_families = ', '.join('"' + family + '"' for family in base_font.families())
            title_families = ', '.join('"' + family + '"' for family in title_font.families())
            # Scoped font rules also defeat a parent's Minecraft font-family
            # stylesheet, not only QWidget font inheritance. Bold title has its
            # own complete Latin+CJK chain; controls remain normal/antialiased.
            self.setStyleSheet('''
                QWidget#cs2_crosshair_editor { background: #17232b; color: #edf0f2; }
                QWidget#cs2_crosshair_editor QWidget { background: transparent; color: #edf0f2; }
                QWidget#cs2_crosshair_editor QGroupBox { color: #d6ad70; border: 1px solid #3d4b54; margin-top: 12px; padding: 12px 8px 6px; }
                QWidget#cs2_crosshair_editor QGroupBox::title { subcontrol-origin: margin; left: 12px; }
                QWidget#cs2_crosshair_editor QScrollArea,
                QWidget#cs2_crosshair_editor QScrollArea > QWidget > QWidget { background: #17232b; border: 0; }
                QWidget#cs2_crosshair_editor QSpinBox, QWidget#cs2_crosshair_editor QDoubleSpinBox,
                QWidget#cs2_crosshair_editor QComboBox, QWidget#cs2_crosshair_editor QLineEdit {
                    color: #f5f6f7; background: #263641; padding: 5px; border: 1px solid #64737d;
                }
                QWidget#cs2_crosshair_editor QComboBox QAbstractItemView { color: #f5f6f7; background: #263641; }
                QWidget#cs2_crosshair_editor QPushButton { color: #f5f6f7; background: #304653; border: 1px solid #72828c; padding: 8px 10px; }
                QWidget#cs2_crosshair_editor QPushButton:hover { color: #18232d; background: #dee4e7; }
                QWidget#cs2_crosshair_editor QCheckBox::indicator { width: 14px; height: 14px; border: 1px solid #72828c; background: #263641; }
                QWidget#cs2_crosshair_editor QCheckBox::indicator:checked { background: #d6ad70; border-color: #d6ad70; }
            ''' + f'''
                QWidget#cs2_crosshair_editor, QWidget#cs2_crosshair_editor QWidget {{
                    font-family: {regular_families}; font-size: 10pt;
                    font-weight: 400; font-style: normal;
                }}
                QWidget#cs2_crosshair_editor QLabel#crosshair_title {{
                    font-family: {title_families}; font-size: 24pt; font-weight: 700;
                }}
            ''')
            self.profile = profile_from_module(host.registry.get('Crosshair'))
            self.controls, self.previews = {}, []
            self.loading = True
            outer = QVBoxLayout(self)
            outer.setContentsMargins(0, 0, 0, 0)
            self.page_scroll = QScrollArea()
            self.page_scroll.setObjectName('crosshair_page_scroll')
            self.page_scroll.setWidgetResizable(True)
            self.content = QWidget()
            self.page_scroll.setWidget(self.content)
            outer.addWidget(self.page_scroll)
            page = QVBoxLayout(self.content)
            page.setContentsMargins(12, 12, 12, 12)
            page.setSpacing(8)
            title = QLabel('CROSSHAIR / 准星')
            title.setWordWrap(True)
            title.setObjectName('crosshair_title')
            title.setFont(title_font)
            page.addWidget(title)
            notice = QLabel('静态近似 / STATIC PREVIEW — RM没有CS散布、后坐力、武器与开镜遥测；这些字段完整保留但不模拟。')
            notice.setWordWrap(True)
            page.addWidget(notice)
            self.body = QBoxLayout(QBoxLayout.Direction.LeftToRight)
            page.addLayout(self.body, 1)
            self.parameter_scroll = QScrollArea()
            self.parameter_scroll.setObjectName('crosshair_parameter_scroll')
            self.parameter_scroll.setWidgetResizable(True)
            self.parameter_scroll.setMinimumSize(250, 400)
            parameters = QWidget()
            groups = QVBoxLayout(parameters)
            groups.setContentsMargins(0, 0, 8, 0)
            self.parameter_scroll.setWidget(parameters)
            self.body.addWidget(self.parameter_scroll, 5)
            display_group = QGroupBox('DISPLAY SIZE / 整体大小（本客户端）')
            display_form = QFormLayout(display_group)
            display_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
            self.display_scale = QDoubleSpinBox()
            self.display_scale.setObjectName('crosshair_display_scale')
            self.display_scale.setRange(*DISPLAY_SCALE_RANGE)
            self.display_scale.setDecimals(1)
            self.display_scale.setSingleStep(.1)
            self.display_scale.setSuffix(' ×')
            self.display_scale.setValue(display_scale_from_module(host.registry.get('Crosshair')))
            self.display_scale.valueChanged.connect(self.refresh)
            display_form.addRow(label('Crosshair Scale'), self.display_scale)
            display_note = QLabel('CS样式采用2×本地显示基准；将整体大小设为0.5×可恢复原参考尺寸。\n同时缩放长度、间距、粗细和描边，不影响自瞄FOV；不修改导入或导出的分享码。')
            display_note.setWordWrap(True)
            display_form.addRow(display_note)
            groups.addWidget(display_group)
            for title, names in (
                ('GEOMETRY / 几何', ('Geometry Units', 'CS2 Style', 'Reference Height', 'Length', 'Gap', 'Thickness', 'Center Dot', 'T Shape')),
                ('COLOR / 颜色与透明度', ('Color Mode', 'Red', 'Green', 'Blue', 'Alpha', 'Use Alpha')),
                ('OUTLINE / 描边', ('Outline Mode', 'Outline Width', 'Outline Red', 'Outline Green', 'Outline Blue', 'Outline Alpha')),
                ('DYNAMIC / 动态参数（保留，不模拟）', ('Follow Recoil', 'Dynamic Spread Limit', 'Split Distance', 'Inner Split Alpha', 'Outer Split Alpha', 'Split Size Ratio', 'Fixed Gap', 'Weapon Gap')),
                ('SCOPE / 瞄准镜参数（保留，不模拟）', ('Scope Dot Scale', 'Scope Dot Uses Color')),
            ):
                group = QGroupBox(title)
                form = QFormLayout(group)
                form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
                form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
                for name in names:
                    if name in BOOLS:
                        control = QCheckBox()
                        control.toggled.connect(self.refresh)
                    elif name in ('Geometry Units', 'CS2 Style', 'Outline Mode', 'Color Mode'):
                        control = QComboBox()
                        control.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
                        control.setMinimumContentsLength(10)
                        if name == 'Geometry Units':
                            for text in ('Pixels', 'Legacy'):
                                control.addItem(label(text), text)
                        elif name == 'CS2 Style':
                            for index, text in enumerate(('Dynamic Cross / 动态十字', 'Dynamic Circle / 动态圆环', 'Dynamic Cross Legacy / 旧动态十字',
                                                         'Static Circle / 静态圆环', 'Static Cross / 静态十字', 'Shot Feedback / 射击反馈',
                                                         'Dot Only / 仅中心点', 'Dynamic Quad / 动态四角', 'Static Square / 静态方框', 'Static Quad / 静态四角')):
                                control.addItem(f'{index} · {text}', index)
                        elif name == 'Outline Mode':
                            for index, text in enumerate(('None / 无', 'Full / 全描边', 'Half / 半描边')):
                                control.addItem(text, index)
                        else:
                            for index, text in enumerate(('Red / 红', 'Green / 绿', 'Yellow / 黄', 'Blue / 蓝', 'Cyan / 青', 'Custom RGB / 自定义')):
                                control.addItem(text, index)
                        control.currentIndexChanged.connect(self.refresh)
                    else:
                        control = QSpinBox() if name in INTEGERS else QDoubleSpinBox()
                        control.setRange(*FIELDS[name])
                        if isinstance(control, QDoubleSpinBox):
                            control.setDecimals(2)
                            control.setSingleStep(.01 if 'Alpha' in name or 'Ratio' in name or name == 'Scope Dot Scale' else .1)
                        control.valueChanged.connect(self.refresh)
                    control.setObjectName('crosshair_' + name.replace(' ', '_'))
                    self.controls[name] = control
                    field_label = QLabel(label(name))
                    field_label.setWordWrap(True)
                    field_label.setMinimumWidth(160)
                    field_label.setMaximumWidth(200)
                    field_label.setToolTip(label(name))
                    form.addRow(field_label, control)
                groups.addWidget(group)
            groups.addStretch()
            self.preview_panel = QWidget()
            self.preview_panel.setMinimumWidth(250)
            right = QVBoxLayout(self.preview_panel)
            right.setContentsMargins(0, 0, 0, 0)
            self.body.addWidget(self.preview_panel, 4)
            preview_options = QFormLayout()
            preview_options.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
            self.resolution = QComboBox()
            self.resolution.addItems(('720', '1080', '1440', '2160'))
            current_height = str(max(1, round(host.height() * host.devicePixelRatioF())))
            if self.resolution.findText(current_height) < 0:
                self.resolution.addItem(current_height)
            self.resolution.setCurrentText(current_height)
            self.resolution.currentIndexChanged.connect(self.refresh)
            self.zoom = QDoubleSpinBox()
            self.zoom.setRange(1, 8)
            self.zoom.setValue(1)
            self.zoom.setSuffix(' ×')
            self.zoom.valueChanged.connect(self.refresh)
            preview_options.addRow('Target height / 目标高度（物理px）', self.resolution)
            preview_options.addRow('Preview zoom / 预览倍数', self.zoom)
            right.addLayout(preview_options)
            for name, colors, grid in (('DARK', ('#152d3c', '#4b6976'), False), ('LIGHT', ('#c4ccd0', '#f4e8c8'), False), ('GRID', ('#282b30', '#4b4741'), True)):
                preview = Preview(self, name, colors, grid)
                self.previews.append(preview)
                right.addWidget(preview, 1)
            self.format = QComboBox()
            self.format.addItems(FORMATS[1:])
            self.format.setCurrentText(self.profile['Source Format'] if self.profile['Source Format'] != 'Custom' else 'cs2-v1')
            self.format.currentIndexChanged.connect(self.refresh)
            export_label = QLabel('EXPORT FORMAT / 导出格式（不兼容字段会阻止导出）')
            export_label.setWordWrap(True)
            right.addWidget(export_label)
            right.addWidget(self.format)
            self.code = QLineEdit()
            self.code.setReadOnly(True)
            self.code.setObjectName('crosshair_share_code')
            right.addWidget(self.code)
            self.status = QLabel()
            self.status.setWordWrap(True)
            self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            right.addWidget(self.status)
            self.actions = QGridLayout()
            self.action_buttons = []
            page.addLayout(self.actions)
            buttons = [('导入文本', self.import_text), ('导入文件', self.import_file), ('复制分享码', self.copy_code),
                       ('导出JSON / CFG / TXT', self.save_file), ('应用', self.apply)]
            if not embedded:
                buttons.append(('关闭', self.reject))
            for text, callback in buttons:
                button = QPushButton(text)
                button.clicked.connect(callback)
                button.setMinimumWidth(0)
                button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
                button.setToolTip(text)
                self.action_buttons.append(button)
            self.load_profile(self.profile)
            self.update_responsive_layout()

        def resizeEvent(self, event):
            super().resizeEvent(event)
            if hasattr(self, 'action_buttons'):
                self.update_responsive_layout()

        def update_responsive_layout(self):
            narrow = self.width() < 900
            self.body.setDirection(QBoxLayout.Direction.TopToBottom if narrow else QBoxLayout.Direction.LeftToRight)
            # Parameters retain their own scroll. On a small display the whole
            # page also scrolls, so previews, notes and action buttons remain
            # reachable instead of forcing an 880x650 minimum onto the host.
            self.parameter_scroll.setMaximumHeight(460 if narrow else 16777215)
            columns = 1 if self.width() < 430 else 2 if self.width() < 650 else 3 if self.width() < 900 else len(self.action_buttons)
            while self.actions.count():
                self.actions.takeAt(0)
            for index, button in enumerate(self.action_buttons):
                self.actions.addWidget(button, index // columns, index % columns)

        def eventFilter(self, watched, event):
            if not embedded and self.isVisible() and event.type() in (
                QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress, QEvent.Type.KeyRelease,
            ) and event.key() == Qt.Key.Key_F12:
                if event.type() == QEvent.Type.KeyPress and not event.isAutoRepeat():
                    _editor_estop(host)
                event.accept()
                return True
            return super().eventFilter(watched, event)

        def draft(self):
            v = dict(self.profile)
            for name, control in self.controls.items():
                if isinstance(control, QCheckBox):
                    v[name] = control.isChecked()
                elif isinstance(control, QComboBox):
                    v[name] = control.currentData()
                else:
                    v[name] = control.value()
            # A manually edited profile is no longer bound to its source enum
            # range; original code remains provenance, not authoritative values.
            v['Source Format'] = 'Custom'
            return validate_profile(v)

        def load_profile(self, values):
            self.loading = True
            self.profile = validate_profile(values)
            for name, control in self.controls.items():
                value = self.profile[name]
                if isinstance(control, QCheckBox):
                    control.setChecked(value)
                elif isinstance(control, QComboBox):
                    control.setCurrentIndex(control.findData(value))
                else:
                    control.setValue(value)
            if self.profile['Source Format'] != 'Custom':
                self.format.setCurrentText(self.profile['Source Format'])
            self.loading = False
            self.refresh()

        def refresh(self, *args):
            if self.loading:
                return
            for preview in self.previews:
                preview.update()
            try:
                v = self.draft()
                self.code.setText(encode_share_code(v, self.format.currentText()))
                self.status.setText('可无损表达全部目标字段。预览静态近似；动态参数不模拟。' + ('\n' + v['Import Notes'] if v['Import Notes'] else ''))
            except ValueError as exc:
                self.code.clear()
                self.status.setText(str(exc) + '\n完整JSON仍可保存所有字段。')

        def import_text(self):
            from PySide6.QtWidgets import QInputDialog
            text, ok = QInputDialog.getMultiLineText(self, '导入准星', 'CS / CSGO分享码、JSON或准星CFG：')
            if ok:
                self.import_value(text)

        def import_value(self, text):
            try:
                self.load_profile(parse_external(text))
                return True
            except (ValueError, UnicodeError) as exc:
                QMessageBox.warning(self, '准星导入失败', str(exc))
                return False

        def import_file(self):
            name, _ = QFileDialog.getOpenFileName(self, '导入准星', '', '准星文件 (*.json *.cfg *.txt)',
                                                options=QFileDialog.Option.DontUseNativeDialog)
            if not name:
                return
            try:
                with Path(name).open('rb') as source:
                    data = source.read(MAX_BYTES + 1)
                if len(data) > MAX_BYTES:
                    raise ValueError('准星配置超过64KiB')
                self.import_value(data.decode('utf-8-sig'))
            except (OSError, UnicodeError, ValueError) as exc:
                QMessageBox.warning(self, '准星导入失败', str(exc))

        def copy_code(self):
            try:
                code = encode_share_code(self.draft(), self.format.currentText())
                QApplication.clipboard().setText(code)
                self.status.setText('已复制：' + code + '\n动态效果仍未模拟。')
            except ValueError as exc:
                QMessageBox.warning(self, '不能无损导出', str(exc))

        def save_file(self):
            name, selected = QFileDialog.getSaveFileName(self, '导出准星（仅写自选文件，不写游戏目录）', 'crosshair.json', '完整JSON (*.json);;白名单CFG (*.cfg);;分享码TXT (*.txt)',
                                                       options=QFileDialog.Option.DontUseNativeDialog)
            if not name:
                return
            try:
                path = Path(name).resolve()
                # Guard both known names and renamed game roots, read-only
                # existence probes only. Never write an installed CS resource.
                if any(p.name.lower() in ('counter-strike global offensive', 'counter-strike 2') or (p / 'game/csgo/pak01_dir.vpk').is_file() for p in path.parents):
                    raise ValueError('禁止向CS2安装目录写入；请选择自己的导出文件夹')
                v = self.draft()
                if path.suffix.lower() == '.json':
                    data = export_json(v)
                elif path.suffix.lower() == '.cfg':
                    data = export_cfg(v, self.format.currentText())
                elif path.suffix.lower() == '.txt':
                    data = encode_share_code(v, self.format.currentText()) + '\n'
                else:
                    raise ValueError('请选择 .json / .cfg / .txt 后缀')
                path.write_text(data, encoding='utf-8')
                self.status.setText('已导出：' + str(path))
            except (OSError, ValueError) as exc:
                QMessageBox.warning(self, '准星导出失败', str(exc))

        def apply(self):
            try:
                v = self.draft()
                scale_setting = host.registry.get('Crosshair').get_setting('Crosshair Scale')
                multiplier = self.display_scale.value()
                if scale_setting is None or not math.isfinite(multiplier) or not DISPLAY_SCALE_RANGE[0] <= multiplier <= DISPLAY_SCALE_RANGE[1]:
                    raise ValueError('准星整体大小无效，未修改设置')
                # Keep the original import format if its constraints still fit.
                candidate = dict(v, **{'Source Format': self.profile['Source Format']})
                try:
                    v = validate_profile(candidate)
                except ValueError:
                    pass
                apply_profile(host.registry.get('Crosshair'), v)
                scale_setting.value = multiplier
                host.invalidate_transient_overlay_scene()
                host.invalidate_gui_cache()
                host.frame_scheduler.force()
                host.auto_save_config()
                self.status.setText('已应用到本客户端（静态近似；动态/recoil/scope参数完整保留，不模拟）。')
            except (ValueError, TypeError) as exc:
                QMessageBox.warning(self, '准星参数无效', str(exc))

    return Editor()


def build_crosshair_editor(host):
    """Return a real QWidget for Settings Center, without exec/input side effects."""
    return _create_editor(host, embedded=True)


def open_crosshair_editor(host):
    """Shared settings-navigation entry point; adds no PolarRMWindow members."""
    # Fail closed before the modal loop. Keep telemetry/safety timers running;
    # closing must never arm control or replay held input.
    host.backend.lock_control('crosshair settings')
    host.control.mock_output = bool(host.backend.mqtt.mock_output)
    host.control.release_all(host.state, host.registry)
    host._control_mouse_point = None
    dialog = _create_editor(host)
    try:
        return dialog.exec()
    finally:
        dialog.deleteLater()


def _editor_estop(host):
    host.control.reset_inputs()
    ok, reason = host.backend.trigger_estop()
    host.control.clear_pending()
    host.toast('E-STOP ACTIVE' if ok else f'E-STOP ACTIVE ({reason})')
