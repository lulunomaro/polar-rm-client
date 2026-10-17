from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable

from PySide6.QtCore import QRectF
from PySide6.QtGui import QImage, QPainter


class ClickGuiLayerCache:
    """ClickGUI 静态图层缓存。

    QOpenGLWidget 上的 QPainter 会把缓存图像作为纹理合成；因此高频动画只
    需要移动、裁剪或混合图层，不必每帧重复生成圆角、阴影与标题文字。
    """

    def __init__(self, capacity: int = 40) -> None:
        self.capacity = max(8, capacity)
        self._images: OrderedDict[tuple[object, ...], QImage] = OrderedDict()

    def clear(self) -> None:
        self._images.clear()

    def draw(
        self,
        painter: QPainter,
        rect: QRectF,
        key: tuple[object, ...],
        build: Callable[[QPainter, QRectF], None],
        *,
        device_pixel_ratio: float = 1.0,
    ) -> None:
        if rect.width() <= 0.5 or rect.height() <= 0.5:
            return
        dpr = max(1.0, device_pixel_ratio)
        cache_key = (
            *key,
            round(rect.width(), 2),
            round(rect.height(), 2),
            round(dpr, 2),
        )
        image = self._images.get(cache_key)
        if image is None:
            image = QImage(
                max(1, round(rect.width() * dpr)),
                max(1, round(rect.height() * dpr)),
                QImage.Format.Format_ARGB32_Premultiplied,
            )
            image.fill(0)
            layer = QPainter(image)
            layer.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            layer.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
            layer.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            layer.scale(dpr, dpr)
            try:
                build(layer, QRectF(0.0, 0.0, rect.width(), rect.height()))
            finally:
                layer.end()
            image.setDevicePixelRatio(dpr)
            self._images[cache_key] = image
            self._images.move_to_end(cache_key)
            while len(self._images) > self.capacity:
                self._images.popitem(last=False)
        else:
            self._images.move_to_end(cache_key)
        painter.drawImage(rect.topLeft(), image)
