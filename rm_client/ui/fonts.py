from __future__ import annotations

from pathlib import Path
from functools import lru_cache

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QFont, QFontDatabase, QGuiApplication, QPaintDevice, QPainter,
    QTextCharFormat, QTextLayout, QTextOption,
)


class FontBook:
    def __init__(self) -> None:
        self.loaded: dict[str, str] = {}
        self._font_cache: dict[tuple[str, str, float, int | None], QFont] = {}
        self._cjk_families_cache: tuple[str, ...] = ()

    def _cjk_families(self) -> tuple[str, ...]:
        if not self._cjk_families_cache:
            available = set(QFontDatabase.families())
            preferred = tuple(family for family in
                              ("Microsoft YaHei UI", "Microsoft YaHei", "DengXian")
                              if family in available)
            system = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont).family()
            self._cjk_families_cache = tuple(dict.fromkeys((*preferred, system or "Sans Serif", "Sans Serif")))
        return self._cjk_families_cache

    def load_from_project_assets(self, workspace: Path, *, keys=None) -> None:
        # A reloaded face may keep its family name. Invalidate configured
        # font values as well as letting Qt invalidate its raster font cache.
        self._font_cache.clear()
        self._cjk_families_cache = ()
        candidates = {
            "axiforma_regular": "axiforma_regular.ttf",
            "axiforma_bold": "axiforma_bold.ttf",
            "axiforma_extrabold": "axiforma_extrabold.ttf",
            "poppins_regular": "Poppins-Regular.ttf",
            "poppins_medium": "Poppins-Medium.ttf",
            "poppins_bold": "Poppins-Bold.ttf",
            "museo": "MuseoSansCyrl-900.ttf",
            "material": "MaterialIcons-Regular.ttf",
            "polaricon": "*icon-Regular.ttf",
            "pingfang": "pingfang_sc_regular.ttf",
            # Debug.jar HUD ArrayList tahoma16 (MODLIST_WHITE.md).
            "tahoma": "tahoma.ttf",
            "jello_light": "Jello-Helvetica-Neue-Light.ttf",
            "jello_medium": "Jello-Helvetica-Neue-Medium.ttf",
            "jello_regular": "Jello-Regular.ttf",
        }
        for key, filename in candidates.items():
            if keys is not None and key not in keys:
                continue
            path = self._find_font(Path(workspace), filename)
            if not path.exists():
                continue
            font_id = QFontDatabase.addApplicationFont(str(path))
            families = QFontDatabase.applicationFontFamilies(font_id)
            if families:
                self.loaded[key] = families[0]

    def _find_font(self, workspace: Path, pattern: str) -> Path:
        direct = workspace / "assets" / "fonts" / pattern
        if direct.exists():
            return direct
        # Bundled faces live only in assets/fonts. A missing optional face
        # must not scan recordings, user data, SDKs or an entire source tree.
        # Keep the icon wildcard and nested font-pack layouts within that root.
        for path in sorted((workspace / "assets" / "fonts").glob("**/" + pattern)):
            if path.is_file():
                return path
        return workspace / "__missing_font__"

    def font(self, key: str, size: float, weight: int | None = None) -> QFont:
        family = self.loaded.get(key)
        if family is None:
            family = {
                "material": "Segoe UI Symbol",
                "polaricon": "Segoe UI",
                "museo": "Segoe UI",
                "pingfang": self._cjk_families()[0],
                "tahoma": "Tahoma",
            }.get(key, "Segoe UI")
        # HUD builders request the same fonts every display frame. Cache the
        # configured value, but return an implicitly shared copy: callers may
        # change its size/style without poisoning later requests. Keep exact
        # sizes (no rounding) and the resolved family in the key, so loading or
        # replacing a font takes effect immediately without a stale fallback.
        # These are logical point-size descriptors, not metrics or pixels;
        # each painter/atlas still applies its own DPI/DPR and raster policy.
        cache_key = (key, family, size, weight)
        cached = self._font_cache.get(cache_key)
        if cached is not None:
            return QFont(cached)
        font = QFont(family)
        # Pin fallback *families* as well as the primary face. This protects
        # direct QPainter callers; mixed Light/Regular runs are shaped below.
        if key not in ("material", "polaricon"):
            font.setFamilies(list(dict.fromkeys((family, *self._cjk_families()))))
        font.setPointSizeF(size)
        if key == "jello_light":
            # The bundled TTF exposes Light as its only named style, but Qt
            # otherwise reports/request a Normal (400) face. Pin both style
            # name and weight so the GPU atlas cannot silently thicken it.
            font.setStyleName("Light")
            if weight is None:
                font.setWeight(QFont.Weight.Light)
        elif key == "jello_medium":
            font.setStyleName("Medium")
            if weight is None:
                font.setWeight(QFont.Weight.Medium)
        if weight is not None:
            font.setWeight(weight)
        if key == "tahoma":
            # jar createFont tahoma16: PLAIN, antiAlias off → pixel-sharp white list.
            font.setBold(False)
            font.setStyleStrategy(QFont.StyleStrategy.NoAntialias)
        # Animated sizes and imported fonts must not grow this cache forever.
        if len(self._font_cache) >= 512:
            self._font_cache.pop(next(iter(self._font_cache)))
        self._font_cache[cache_key] = QFont(font)
        return font


# Shared UI helpers are module-level, not new PolarRMWindow members: its public
# TextMetricsMixin member names/signatures/qualnames are frozen. Resolve the
# database once, never enumerate installed fonts in a display-frame hot path.
_ui_fonts = FontBook()


def cjk_ui_font(font: QFont) -> QFont:
    result = QFont(font)
    result.setFamilies(list(_ui_fonts._cjk_families()))
    result.setStyleName("Regular")
    result.setWeight(QFont.Weight.Normal)
    result.setStretch(100)
    result.setStyleHint(QFont.StyleHint.SansSerif)
    result.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    result.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
    result.setKerning(True)
    return result


def _is_cjk(character: str) -> bool:
    codepoint = ord(character)
    return any(start <= codepoint <= end for start, end in (
        (0x2E80, 0x2FFF), (0x3000, 0x30FF), (0x3100, 0x312F),
        (0x31A0, 0x31BF), (0x31F0, 0x31FF), (0x3400, 0x4DBF),
        (0x4E00, 0x9FFF), (0xAC00, 0xD7AF), (0xF900, 0xFAFF),
        (0xFF00, 0xFFEF), (0x20000, 0x2FA1F), (0x30000, 0x323AF),
    ))


@lru_cache(maxsize=4096)
def has_cjk(text: str) -> bool:
    return any(_is_cjk(character) for character in text)


def ui_font_key(font: QFont, device: QPaintDevice | None = None) -> tuple[object, ...]:
    # QFont.key() also omits kerning and overline. A non-kerning request must
    # not reuse a shaped Latin run (including Latin inside a Chinese label).
    screen = QGuiApplication.primaryScreen() if device is None else None
    strategy, hinting = font.styleStrategy(), font.hintingPreference()
    return (font.key(), tuple(font.families()), font.styleName(),
            # Qt 6's Python Flag cannot be int()-cast; older Shiboken enums
            # are already integer-like and need not expose .value.
            getattr(strategy, "value", strategy), getattr(hinting, "value", hinting),
            font.kerning(), font.overline(),
            float(device.logicalDpiX()) if device is not None else float(screen.logicalDotsPerInchX()) if screen else 96.0,
            float(device.logicalDpiY()) if device is not None else float(screen.logicalDotsPerInchY()) if screen else 96.0,
            float(device.devicePixelRatioF()) if device is not None else 1.0)


_ui_layout_cache: dict[tuple[object, ...], QTextLayout] = {}


def ui_text_layout(font: QFont, text: str, device: QPaintDevice | None = None) -> QTextLayout:
    """One cached, device-matched baseline with original Latin and Regular CJK.

    Never sum per-character widths: that loses kerning/ligatures and fallback
    ascenders. FormatRange uses QString's UTF-16 offsets, not Python indices.
    """
    text = str(text)
    key = (ui_font_key(font, device), text, _ui_fonts._cjk_families())
    cached = _ui_layout_cache.get(key)
    if cached is not None:
        return cached
    layout = QTextLayout(text, font, device)
    option = QTextOption()
    option.setWrapMode(QTextOption.WrapMode.NoWrap)
    option.setUseDesignMetrics(False)
    layout.setTextOption(option)
    ranges = []
    start, offset = -1, 0
    cjk_font = cjk_ui_font(font)
    for character in text + "\0":
        if _is_cjk(character):
            if start < 0:
                start = offset
        elif start >= 0:
            span = QTextLayout.FormatRange()
            span.start, span.length = start, offset - start
            span.format = QTextCharFormat()
            span.format.setFont(cjk_font)
            ranges.append(span)
            start = -1
        offset += 2 if ord(character) > 0xFFFF else 1
    layout.setFormats(ranges)
    layout.setCacheEnabled(True)
    layout.beginLayout()
    line = layout.createLine()
    if line.isValid():
        line.setLineWidth(1.0e7)
        line.setPosition(QPointF(0, 0))
    layout.endLayout()
    ink = QRectF()
    for run in layout.glyphRuns():
        raw = run.rawFont()
        for glyph, position in zip(run.glyphIndexes(), run.positions()):
            ink = ink.united(raw.boundingRect(glyph).translated(position))
    layout._ui_ink_bounds = ink
    if len(_ui_layout_cache) >= 4096:
        _ui_layout_cache.pop(next(iter(_ui_layout_cache)))
    _ui_layout_cache[key] = layout
    return layout


def ui_text_advance(font: QFont, text: str, device: QPaintDevice | None = None) -> float:
    layout = ui_text_layout(font, text, device)
    return float(layout.lineAt(0).naturalTextWidth()) if layout.lineCount() else 0.0


_ui_elide_cache: dict[tuple[object, ...], str] = {}


def elide_ui_text(font: QFont, text: str, width: float, device: QPaintDevice | None = None) -> str:
    key = (ui_font_key(font, device), text, float(width), _ui_fonts._cjk_families())
    result = _ui_elide_cache.get(key)
    if result is None:
        result = _elide_ui_text(font, text, width, device)
        if len(_ui_elide_cache) >= 4096:
            _ui_elide_cache.pop(next(iter(_ui_elide_cache)))
        _ui_elide_cache[key] = result
    return result


def _elide_ui_text(font: QFont, text: str, width: float, device: QPaintDevice | None = None) -> str:
    """Elide against the shaped line, including the ellipsis' actual advance."""
    if width <= 0:
        return ""
    if ui_text_advance(font, text, device) <= width:
        return text
    if ui_text_advance(font, "…", device) > width:
        return ""
    # Binary search legal grapheme boundaries; don't split surrogate pairs,
    # combining marks or emoji sequences while shortening mixed UI labels.
    from PySide6.QtCore import QTextBoundaryFinder
    finder = QTextBoundaryFinder(QTextBoundaryFinder.BoundaryType.Grapheme, text)
    offsets = {0: 0}
    offset = 0
    for index, character in enumerate(text):
        offset += 2 if ord(character) > 0xFFFF else 1
        offsets[offset] = index + 1
    boundaries = [0]
    while (offset := finder.toNextBoundary()) >= 0:
        if offset in offsets:
            boundaries.append(offsets[offset])
    lo, hi = 0, len(boundaries) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if ui_text_advance(font, text[:boundaries[mid]] + "…", device) <= width:
            lo = mid
        else:
            hi = mid - 1
    return text[:boundaries[lo]] + "…"


def draw_ui_text(p: QPainter, rect: QRectF, text: str, font: QFont,
                 flags: Qt.AlignmentFlag, *, ink_center: bool = False) -> None:
    layout = ui_text_layout(font, text, p.device())
    if not layout.lineCount():
        return
    line = layout.lineAt(0)
    width, height = float(line.naturalTextWidth()), float(line.height())
    x, y = rect.left(), rect.top()
    if flags & Qt.AlignmentFlag.AlignHCenter:
        x += (rect.width() - width) * .5
    elif flags & Qt.AlignmentFlag.AlignRight:
        x += rect.width() - width
    if flags & Qt.AlignmentFlag.AlignVCenter:
        y += (rect.height() - height) * .5
    elif flags & Qt.AlignmentFlag.AlignBottom:
        y += rect.height() - height
    if ink_center:
        # Small scoreboard rows reserve less space than a full YaHei line.
        # Center the *actual ink*, without squeezing Chinese glyphs or clipping
        # them to Latin ascent/descent. Keep the painter's external clip intact.
        ink = layout._ui_ink_bounds
        if not ink.isEmpty():
            y = rect.center().y() - ink.center().y()
    # Snap the common baseline only, not individual advances. DPR and the
    # current HUD transform both matter; retain subpixel shaping within a run.
    transform = p.deviceTransform()
    if abs(transform.m12()) < 1e-6 and abs(transform.m21()) < 1e-6 and abs(transform.m22()) > 1e-6:
        baseline = y + float(line.ascent())
        physical = transform.map(QPointF(x, baseline))
        y += (round(physical.y()) - physical.y()) / transform.m22()
    layout.draw(p, QPointF(x, y))
