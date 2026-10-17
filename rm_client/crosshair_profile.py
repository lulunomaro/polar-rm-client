"""Pure crosshair profile schema, validation and lossless wire interchange.

No UI, registry, filesystem or runtime dependency belongs here. Settings mutation,
local presentation preferences and Qt preview/editor effects remain in the
cs2_crosshair adapter. The adapter re-exports the historic codec API.

Protocol/layout reference and factual vectors: akiver/csgo-sharecode (MIT),
https://github.com/akiver/csgo-sharecode/blob/main/doc/code_layout.md
Independently implemented from that format; accessed 2026-10-14.
"""
from __future__ import annotations

import json
import math
import re

ALPHABET = "ABCDEFGHJKLMNOPQRSTUVWXYZabcdefhijkmnopqrstuvwxyz23456789"

MAX_BYTES = 64 * 1024

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
