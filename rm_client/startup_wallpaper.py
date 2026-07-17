from __future__ import annotations

import json
import math
import shutil
import struct
import subprocess
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPainterPath


PLAYABLE_EXTENSIONS = {".mp4", ".webm", ".mov", ".avi", ".mkv", ".gif", ".png", ".jpg", ".jpeg"}


@dataclass(frozen=True)
class PkgEntry:
    name: str
    offset: int
    size: int


@dataclass(frozen=True)
class PkgSceneLayer:
    """A texture layer positioned in the Wallpaper Engine scene coordinate space."""

    image: QImage
    left: float
    top: float
    width: float
    height: float
    alpha: float


@dataclass(frozen=True)
class VideoFrameSnapshot:
    """视频线程发布给界面线程的不可变帧快照。"""

    path: Path | None
    image: QImage
    soft_glass: QImage
    glass: QImage
    sequence: int
    source_fps: float
    published_at: float
    frame_interval: float


class PkgVirtualFileSystem:
    """Minimal reader for Wallpaper Engine PKGV packages."""

    def __init__(self, package_path: Path) -> None:
        self.package_path = Path(package_path)
        self.entries: dict[str, PkgEntry] = {}
        self.data_start = 0
        self._index_loaded = False

    def load_index(self) -> None:
        if self._index_loaded:
            return
        data = self.package_path.read_bytes()
        if len(data) < 16 or data[4:12] != b"PKGV0018":
            raise ValueError("Unsupported Wallpaper Engine package")
        count = struct.unpack_from("<I", data, 12)[0]
        pos = 16
        entries: dict[str, PkgEntry] = {}
        for _ in range(count):
            if pos + 4 > len(data):
                raise ValueError("Broken Wallpaper Engine package index")
            name_len = struct.unpack_from("<I", data, pos)[0]
            pos += 4
            name = data[pos : pos + name_len].decode("utf-8", errors="replace")
            pos += name_len
            offset, size = struct.unpack_from("<II", data, pos)
            pos += 8
            entries[name] = PkgEntry(name, offset, size)
        self.entries = entries
        self.data_start = pos
        self._index_loaded = True

    def has(self, name: str) -> bool:
        self.load_index()
        return name in self.entries

    def read_bytes(self, name: str) -> bytes:
        self.load_index()
        entry = self.entries[name]
        with self.package_path.open("rb") as handle:
            handle.seek(self.data_start + entry.offset)
            return handle.read(entry.size)

    def read_text(self, name: str) -> str:
        return self.read_bytes(name).decode("utf-8", errors="replace")

    def read_json(self, name: str) -> Any:
        return json.loads(self.read_text(name))


class VideoWallpaperPlayer:
    def __init__(self) -> None:
        self.path: Path | None = None
        self._published_path: Path | None = None
        self._image = QImage()
        self._soft_glass = QImage()
        self._glass = QImage()
        self._sequence = 0
        self._source_fps = 30.0
        self._published_at = 0.0
        self._frame_interval = 1.0 / 30.0
        self._target_size = (1280, 720)
        self._motion_quality = "Balanced30"
        self._active = True
        self._last_error = ""
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._generation = 0
        self._glass_frame_counter = 0
        self._cached_soft = QImage()
        self._cached_glass = QImage()

    @property
    def image(self) -> QImage:
        return self.snapshot().image

    @property
    def last_error(self) -> str:
        with self._lock:
            return self._last_error

    def configure(self, target_size: tuple[int, int], motion_quality: str) -> None:
        with self._lock:
            self._target_size = (max(320, int(target_size[0])), max(180, int(target_size[1])))
            self._motion_quality = motion_quality if motion_quality in {"Smooth60", "Balanced30", "Source"} else "Balanced30"

    def set_active(self, active: bool) -> None:
        with self._lock:
            self._active = bool(active)

    def load(self, path: Path) -> bool:
        path = Path(path).resolve()
        if not path.exists():
            return False
        with self._lock:
            if self.path == path and self._thread is not None and self._thread.is_alive():
                return True
            if self.path == path and self._last_error:
                return False
        self._stop_worker(keep_frame=True, wait=False)
        with self._lock:
            self.path = path
            self._generation += 1
            generation = self._generation
            self._last_error = ""
            self._stop_event = threading.Event()
            thread = threading.Thread(
                target=self._decode_loop,
                args=(path, generation, self._stop_event),
                name=f"wallpaper:{path.stem}",
                daemon=True,
            )
            self._thread = thread
        thread.start()
        return True

    def release(self, keep_frame: bool = False) -> None:
        self._stop_worker(keep_frame=keep_frame, wait=True)

    def _stop_worker(self, keep_frame: bool, wait: bool) -> None:
        with self._lock:
            thread = self._thread
            stop_event = self._stop_event
            self._generation += 1
            self._thread = None
            self.path = None
        stop_event.set()
        if wait and thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=0.8)
        if not keep_frame:
            with self._lock:
                self._published_path = None
                self._image = QImage()
                self._soft_glass = QImage()
                self._glass = QImage()
                self._cached_soft = QImage()
                self._cached_glass = QImage()
                self._glass_frame_counter = 0

    def tick(self) -> None:
        """保留旧调用入口；视频解码已经完全由后台线程驱动。"""

    def snapshot(self) -> VideoFrameSnapshot:
        with self._lock:
            return VideoFrameSnapshot(
                self._published_path,
                self._image,
                self._soft_glass,
                self._glass,
                self._sequence,
                self._source_fps,
                self._published_at,
                self._frame_interval,
            )

    def _decode_loop(self, path: Path, generation: int, stop_event: threading.Event) -> None:
        try:
            import cv2  # type: ignore
        except ImportError as exc:
            self._set_error(generation, str(exc))
            return

        static_image = path.suffix.lower() in {".png", ".jpg", ".jpeg"}
        capture: Any = None
        smoothed_lens: Any | None = None
        try:
            if static_image:
                frame = cv2.imread(str(path), cv2.IMREAD_COLOR)
                if frame is None:
                    self._set_error(generation, "无法读取图片壁纸")
                    return
                main, soft_glass, glass, _ = self._prepare_frames(frame, cv2, None)
                self._publish(generation, path, main, soft_glass, glass, 1.0)
                while not stop_event.wait(0.10):
                    pass
                return

            capture = cv2.VideoCapture(str(path))
            if not capture.isOpened():
                self._set_error(generation, "无法打开视频壁纸")
                return
            source_fps = max(8.0, min(60.0, float(capture.get(cv2.CAP_PROP_FPS) or 30.0)))
            while not stop_event.is_set():
                with self._lock:
                    active = self._active and generation == self._generation
                    quality = self._motion_quality
                if not active:
                    if generation != self._generation:
                        break
                    stop_event.wait(0.025)
                    continue

                # Balanced30 强制约 30fps 发布；落后时跳读源帧，避免积压。
                target_fps = {
                    "Smooth60": min(60.0, source_fps),
                    "Balanced30": min(30.0, source_fps),
                    "Source": source_fps,
                }.get(quality, min(30.0, source_fps))
                interval = 1.0 / max(8.0, target_fps)
                loop_started = time.perf_counter()

                ok, frame = capture.read()
                if not ok:
                    capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    ok, frame = capture.read()
                if not ok:
                    self._set_error(generation, "视频循环读取失败")
                    break
                main, soft_glass, glass, smoothed_lens = self._prepare_frames(frame, cv2, smoothed_lens)
                self._publish(generation, path, main, soft_glass, glass, target_fps)

                spent = time.perf_counter() - loop_started
                wait_for = interval - spent
                if wait_for > 0:
                    stop_event.wait(wait_for)
                elif spent > interval * 1.75:
                    # 解码过慢：跳过若干源帧，尽快追上实时。
                    skip = min(3, max(1, int(spent / interval) - 1))
                    for _ in range(skip):
                        if stop_event.is_set():
                            break
                        capture.grab()
        except Exception as exc:
            self._set_error(generation, str(exc))
        finally:
            if capture is not None:
                try:
                    capture.release()
                except Exception:
                    pass

    def _prepare_frames(self, frame: Any, cv2: Any, previous_lens: Any | None) -> tuple[QImage, QImage, QImage, Any]:
        with self._lock:
            target_size = self._target_size
            quality = self._motion_quality
            self._glass_frame_counter += 1
            glass_counter = self._glass_frame_counter
            cached_soft = self._cached_soft
            cached_glass = self._cached_glass
        height, width = frame.shape[:2]
        # 启动页不需要接近 1080p 的背景解码；卡顿主要来自 4K 源每帧 resize+模糊。
        max_pixels = {
            "Smooth60": 1280 * 720,
            "Balanced30": 960 * 540,
            "Source": 1600 * 900,
        }.get(quality, 960 * 540)
        max_pixels = min(max_pixels, max(320 * 180, target_size[0] * target_size[1]))
        if width * height > max_pixels:
            ratio = math.sqrt(max_pixels / float(width * height))
            new_w = max(2, round(width * ratio))
            new_h = max(2, round(height * ratio))
            # 大幅降采样用 AREA 更干净；小幅缩放用 LINEAR 更快。
            interp = cv2.INTER_AREA if ratio < 0.72 else cv2.INTER_LINEAR
            frame = cv2.resize(frame, (new_w, new_h), interpolation=interp)
            height, width = frame.shape[:2]

        main_image = self._to_qimage(frame, cv2)

        # 玻璃采样图更小，且隔帧更新 blur，显著减负。
        glass_width = min({"Smooth60": 288, "Balanced30": 224, "Source": 320}.get(quality, 224), width)
        refresh_glass = (
            previous_lens is None
            or cached_soft.isNull()
            or cached_glass.isNull()
            or glass_counter % (1 if quality == "Source" else 2) == 0
        )
        if refresh_glass:
            glass_height = max(2, round(height * glass_width / max(1, width)))
            glass_base = cv2.resize(frame, (glass_width, glass_height), interpolation=cv2.INTER_AREA)
            soft_sigma = 2.0 if quality == "Balanced30" else 2.4
            lens_sigma = 4.8 if quality == "Balanced30" else 6.0
            soft_bgr = cv2.GaussianBlur(glass_base, (0, 0), soft_sigma)
            lens_bgr = cv2.GaussianBlur(glass_base, (0, 0), lens_sigma)
            if previous_lens is not None and previous_lens.shape == lens_bgr.shape:
                # 源帧边界做低通平滑，避免玻璃纹理在 25/30 FPS 视频下跳闪。
                lens_bgr = cv2.addWeighted(lens_bgr, 0.36, previous_lens, 0.64, 0)
            soft_image = self._to_qimage(soft_bgr, cv2)
            glass_image = self._to_qimage(lens_bgr, cv2)
            with self._lock:
                self._cached_soft = soft_image
                self._cached_glass = glass_image
            return main_image, soft_image, glass_image, lens_bgr

        return main_image, cached_soft, cached_glass, previous_lens

    @staticmethod
    def _to_qimage(frame: Any, cv2: Any) -> QImage:
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        height, width, channels = rgb.shape
        # copy 保证线程间发布后 numpy 缓冲可释放，避免悬空。
        return QImage(rgb.data, width, height, channels * width, QImage.Format.Format_RGB888).copy()

    def _publish(self, generation: int, path: Path, image: QImage, soft_glass: QImage, glass: QImage, fps: float) -> None:
        with self._lock:
            if generation != self._generation:
                return
            self._published_path = path
            self._image = image
            self._soft_glass = soft_glass
            self._glass = glass
            self._sequence += 1
            self._source_fps = fps
            self._frame_interval = 1.0 / max(1.0, fps)
            self._published_at = time.perf_counter()

    def _set_error(self, generation: int, message: str) -> None:
        with self._lock:
            if generation == self._generation:
                self._last_error = message


class PkgSceneRenderer:
    def __init__(self, workspace: Path) -> None:
        self.workspace = Path(workspace)
        self.wallpaper_dir = self.workspace / "3000925581"
        self.package_path = self.wallpaper_dir / "scene.pkg"
        self.project_path = self.wallpaper_dir / "project.json"
        self.cache_dir = self.workspace / "rm_custom_client" / ".wallpaper_cache" / "3000925581"
        self._vfs: PkgVirtualFileSystem | None = None
        self._scene: dict[str, Any] | None = None
        self._project_title = "Default3000925581Pkg"
        self._images: dict[str, QImage] = {}
        self._scene_layers: list[PkgSceneLayer] | None = None
        self._scene_image = QImage()
        self._last_error = ""
        self.cache_attempted = False

    @property
    def last_error(self) -> str:
        return self._last_error

    def available(self) -> bool:
        return self.package_path.exists() and self.project_path.exists()

    def load_metadata(self) -> bool:
        if not self.available():
            self._last_error = "Wallpaper package missing"
            return False
        try:
            if self._vfs is None:
                self._vfs = PkgVirtualFileSystem(self.package_path)
                self._vfs.load_index()
            if self._scene is None and self._vfs.has("scene.json"):
                self._scene = self._vfs.read_json("scene.json")
            if self.project_path.exists():
                project = json.loads(self.project_path.read_text(encoding="utf-8", errors="replace"))
                self._project_title = str(project.get("title") or self._project_title)
        except Exception as exc:
            self._last_error = str(exc)
            return False
        return True

    def ensure_cache(self) -> None:
        if self.cache_attempted:
            return
        self.cache_attempted = True
        if self._required_cache_files_exist():
            return
        repkg = self.workspace / "RePKG-GUI_v1.0.1" / "RePKG-GUI_v1.0.1" / "RePKG.exe"
        if not repkg.exists() or not self.package_path.exists():
            return
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.run(
                [str(repkg), "extract", "--overwrite", "-o", str(self.cache_dir), "scene.pkg"],
                cwd=str(self.wallpaper_dir),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=15,
                check=False,
            )
            # The cache may have just appeared, so invalidate any failed lookup.
            self._scene_layers = None
            self._scene_image = QImage()
        except Exception as exc:
            self._last_error = str(exc)

    def draw(self, p: QPainter, rect: QRectF, mouse: QPointF, dt: float, parallax: float, zoom: float, dim: float) -> bool:
        if not self.load_metadata():
            return False
        self.ensure_cache()
        scene_image = self._composed_scene_image()
        if scene_image.isNull():
            return False
        width = rect.width()
        height = rect.height()
        nx = (mouse.x() / max(1.0, width) - 0.5) * 2.0
        ny = (mouse.y() / max(1.0, height) - 0.5) * 2.0
        # 视差会移动整张场景，因此必须以 cover 为基准并预留位移安全边，不能使用 contain。
        safety = max(8.0, abs(parallax) * 0.42 + 8.0)
        safe_zoom = max(1.0 + safety * 2.0 / max(1.0, width), 1.0 + safety * 0.56 / max(1.0, height))
        view_scale = max(width / scene_image.width(), height / scene_image.height()) * max(safe_zoom, min(1.35, max(1.0, zoom)))
        target_w = scene_image.width() * view_scale
        target_h = scene_image.height() * view_scale
        offset_x = -nx * parallax * 0.42
        offset_y = -ny * parallax * 0.24
        draw_rect = QRectF(
            rect.center().x() - target_w / 2.0 + offset_x,
            rect.center().y() - target_h / 2.0 + offset_y,
            target_w,
            target_h,
        )

        p.save()
        p.setClipRect(rect)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        p.fillRect(rect, QColor(56, 110, 128))
        p.drawImage(draw_rect, scene_image)
        shade = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        shade.setColorAt(0.0, QColor(0, 0, 0, int(48 + dim * 70)))
        shade.setColorAt(0.55, QColor(0, 0, 0, int(26 + dim * 64)))
        shade.setColorAt(1.0, QColor(0, 0, 0, int(78 + dim * 82)))
        p.fillRect(rect, shade)
        p.restore()
        return True

    def _composed_scene_image(self) -> QImage:
        if not self._scene_image.isNull():
            return self._scene_image
        layers = self._scene_layers_from_cache()
        if not layers:
            return QImage()

        # The workshop entry is authored for a 3840 x 2160 output.  Caching at
        # half resolution preserves that framing and avoids re-compositing its
        # high-resolution layers every paint event.
        result = QImage(1920, 1080, QImage.Format.Format_ARGB32_Premultiplied)
        result.fill(QColor(66, 177, 236))
        painter = QPainter(result)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        scene_scale = 0.5
        for layer in layers:
            draw_rect = QRectF(
                layer.left * scene_scale,
                layer.top * scene_scale,
                layer.width * scene_scale,
                layer.height * scene_scale,
            )
            painter.setOpacity(layer.alpha)
            painter.drawImage(draw_rect, layer.image)
        painter.end()
        self._scene_image = result
        return self._scene_image

    def _scene_layers_from_cache(self) -> list[PkgSceneLayer]:
        if self._scene_layers is not None:
            return self._scene_layers
        if not self._scene or not self.cache_dir.exists():
            self._scene_layers = []
            return self._scene_layers

        layers: list[PkgSceneLayer] = []
        for item in self._scene.get("objects", []):
            if not isinstance(item, dict) or not item.get("visible", True):
                continue
            model_ref = item.get("image")
            if not isinstance(model_ref, str) or not model_ref.startswith("models/") or "/util/" in model_ref:
                continue
            model = self._cached_json(model_ref)
            if not isinstance(model, dict):
                continue
            material_ref = model.get("material")
            material = self._cached_json(material_ref) if isinstance(material_ref, str) else None
            texture = self._material_texture(material)
            if not texture:
                continue
            image = self._cached_texture(texture)
            if image.isNull():
                continue
            origin_x, origin_y = self._numbers(item.get("origin"), (1920.0, 1080.0))[:2]
            width, height = self._numbers(item.get("size"), (float(image.width()), float(image.height())))[:2]
            scale_x, scale_y = self._numbers(item.get("scale"), (1.0, 1.0))[:2]
            alpha = float(item.get("alpha", 1.0) or 0.0)
            brightness = float(item.get("brightness", 1.0) or 0.0)
            if width <= 0 or height <= 0 or alpha <= 0:
                continue
            draw_width = abs(width * scale_x)
            draw_height = abs(height * scale_y)
            # RePKG exports the already autosized texture while `scene.json`
            # stores its final origin and dimensions.  Applying `cropoffset`
            # again would compensate the trim twice and split the character
            # parts apart, so the final centered geometry is the correct one.
            left = origin_x - draw_width * 0.5
            top = origin_y - draw_height * 0.5
            layers.append(
                PkgSceneLayer(
                    image=image,
                    left=left,
                    top=top,
                    width=draw_width,
                    height=draw_height,
                    alpha=max(0.0, min(1.0, alpha * brightness)),
                )
            )
        self._scene_layers = layers
        return layers

    def _cached_json(self, relative_path: str) -> dict[str, Any] | None:
        path = self.cache_dir.joinpath(*relative_path.replace("\\", "/").split("/"))
        if not path.exists():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8", errors="replace"))
            return value if isinstance(value, dict) else None
        except (OSError, ValueError, json.JSONDecodeError):
            return None

    def _material_texture(self, material: dict[str, Any] | None) -> str | None:
        if not material:
            return None
        for render_pass in material.get("passes", []):
            if not isinstance(render_pass, dict):
                continue
            for texture in render_pass.get("textures", []):
                if isinstance(texture, str) and texture and "mask" not in texture.lower():
                    return texture
        return None

    def _cached_texture(self, texture: str) -> QImage:
        relative = texture if texture.lower().endswith(".png") else f"{texture}.png"
        path = self.cache_dir / "materials" / Path(*relative.replace("\\", "/").split("/"))
        key = str(path)
        image = self._images.get(key)
        if image is None:
            image = QImage(key)
            self._images[key] = image
        return image

    @staticmethod
    def _numbers(value: Any, fallback: tuple[float, ...]) -> tuple[float, ...]:
        if isinstance(value, str):
            try:
                numbers = tuple(float(part) for part in value.split())
                if len(numbers) >= len(fallback):
                    return numbers
            except ValueError:
                pass
        return fallback

    def _required_cache_files_exist(self) -> bool:
        return any(self.cache_dir.rglob("*.png"))

    def _scene_images(self) -> list[tuple[int, Path, QImage]]:
        if not self.cache_dir.exists():
            return []
        pngs = sorted(self.cache_dir.rglob("*.png"), key=lambda path: path.stat().st_size if path.exists() else 0, reverse=True)
        selected: list[Path] = []
        clouds = [path for path in pngs if "clouds2" in path.name.lower()]
        selected.extend(clouds[:1])
        selected.extend([path for path in pngs if path not in selected][:10])

        layers: list[tuple[int, Path, QImage]] = []
        for path in selected:
            key = str(path)
            image = self._images.get(key)
            if image is None:
                image = QImage(key)
                self._images[key] = image
            if image.isNull():
                continue
            order = self._order_for(path, image)
            layers.append((order, path, image))
        layers.sort(key=lambda item: item[0])
        return layers[:8]

    def _order_for(self, path: Path, image: QImage) -> int:
        name = path.name.lower()
        if "clouds" in name:
            return 10
        aspect = image.width() / max(1, image.height())
        if aspect > 1.25:
            return 20
        if image.height() > image.width():
            return 40
        return 30

    def _depth_for(self, path: Path, order: int) -> float:
        name = path.name.lower()
        if "clouds" in name:
            return 0.35
        if order >= 40:
            return 0.82
        return 0.58

    def _opacity_for(self, path: Path, order: int) -> float:
        name = path.name.lower()
        if "mask" in str(path).lower():
            return 0.28
        if "clouds" in name:
            return 0.42
        return 1.0

    def _layer_rect_for(self, image: QImage, rect: QRectF, zoom: float, order: int) -> QRectF:
        canvas_w = rect.width() * max(1.0, zoom)
        canvas_h = rect.height() * max(1.0, zoom)
        image_aspect = image.width() / max(1, image.height())
        canvas_aspect = canvas_w / max(1.0, canvas_h)
        if image_aspect > canvas_aspect:
            target_h = canvas_h
            target_w = target_h * image_aspect
        else:
            target_w = canvas_w
            target_h = target_w / max(0.01, image_aspect)
        x = rect.center().x() - target_w / 2.0
        y = rect.center().y() - target_h / 2.0
        if order >= 40:
            y += rect.height() * 0.08
            x += rect.width() * 0.16
        elif order <= 10:
            y -= rect.height() * 0.18
        return QRectF(x, y, target_w, target_h)


class StartupWallpaperManager:
    def __init__(self, workspace: Path) -> None:
        self.workspace = Path(workspace)
        self.assets_dir = self.workspace / "rm_custom_client" / "assets" / "wallpapers"
        self.builtin_dir = self.assets_dir / "builtin"
        self.texas_video = self.builtin_dir / "texas.mp4"
        self.vending_video = self.builtin_dir / "vending-machine.mp4"
        self.user_dir = self.assets_dir / "user"
        self.pkg_scene = PkgSceneRenderer(self.workspace)
        self.video_player = VideoWallpaperPlayer()
        self.last_frame = QImage()
        self.last_draw_rect = QRectF()
        self.soft_glass_frame = QImage()
        self.glass_frame = QImage()
        self.glass_sequence = -1
        self.parallax_x = 0.0
        self.parallax_y = 0.0
        self.requested_media: Path | None = None
        self.transition_frame = QImage()
        self.transition_started = 0.0
        self.thumbnail_cache: dict[str, QImage] = {}
        self.thumbnail_futures: dict[str, Future[QImage]] = {}
        self.thumbnail_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="wallpaper-thumb")
        self.fast_background_sampling = False
        self.failed_media: Path | None = None
        self.selected_profile = ""
        self.static_glass_key = 0

    def profile_names(self) -> list[str]:
        names = ["TexasMP4", "VendingMachine4K", "Default3000925581Pkg", "ParticleGrid"]
        if self.user_dir.exists():
            for path in sorted(self.user_dir.iterdir()):
                if path.is_file() and path.suffix.lower() in PLAYABLE_EXTENSIONS:
                    names.append(f"User:{path.stem}")
        return names

    def resolve_media_profile(self, profile: str) -> Path | None:
        if profile == "TexasMP4" and self.texas_video.exists():
            return self.texas_video
        if profile == "VendingMachine4K" and self.vending_video.exists():
            return self.vending_video
        return self.resolve_user_profile(profile)

    def profile_label(self, profile: str) -> str:
        return {
            "TexasMP4": "德克萨斯",
            "VendingMachine4K": "Vending Machine",
            "Default3000925581Pkg": "Wallpaper Engine Scene",
            "ParticleGrid": "粒子网格",
        }.get(profile, profile.split(":", 1)[1] if profile.startswith("User:") else profile)

    def profile_kind(self, profile: str) -> str:
        if profile == "Default3000925581Pkg":
            return "PKG SCENE"
        if profile == "ParticleGrid":
            return "GENERATED"
        path = self.resolve_media_profile(profile)
        if path is None:
            return "MISSING"
        suffix = path.suffix.lower().lstrip(".").upper()
        return f"USER {suffix}" if profile.startswith("User:") else f"BUILT-IN {suffix}"

    @staticmethod
    def can_remove(profile: str) -> bool:
        return profile.startswith("User:")

    def import_wallpaper(self, source: Path) -> str:
        source = Path(source)
        if source.suffix.lower() not in PLAYABLE_EXTENSIONS or not source.exists():
            raise ValueError("Unsupported wallpaper file")
        self.user_dir.mkdir(parents=True, exist_ok=True)
        safe_stem = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in source.stem).strip("_") or "wallpaper"
        target = self.user_dir / f"{safe_stem}{source.suffix.lower()}"
        suffix = 1
        while target.exists() and source.resolve() != target.resolve():
            target = self.user_dir / f"{safe_stem}_{suffix}{source.suffix.lower()}"
            suffix += 1
        if source.resolve() != target.resolve():
            shutil.copyfile(source, target)
        return f"User:{target.stem}"

    def resolve_user_profile(self, profile: str) -> Path | None:
        if not profile.startswith("User:") or not self.user_dir.exists():
            return None
        stem = profile.split(":", 1)[1]
        for path in self.user_dir.iterdir():
            if path.is_file() and path.stem == stem and path.suffix.lower() in PLAYABLE_EXTENSIONS:
                return path
        return None

    def remove_wallpaper(self, profile: str) -> bool:
        path = self.resolve_user_profile(profile)
        if path is None:
            return False
        try:
            path.unlink()
        except OSError:
            return False
        self.thumbnail_cache.pop(profile, None)
        future = self.thumbnail_futures.pop(profile, None)
        if future is not None:
            future.cancel()
        return True

    def set_active(self, active: bool) -> None:
        self.video_player.set_active(active)

    def release(self) -> None:
        self.video_player.release()
        self.thumbnail_executor.shutdown(wait=False, cancel_futures=True)

    def thumbnail(self, profile: str) -> QImage:
        cached = self.thumbnail_cache.get(profile)
        if cached is not None:
            return cached
        future = self.thumbnail_futures.get(profile)
        if future is not None:
            if future.done():
                try:
                    image = future.result()
                except Exception:
                    image = QImage()
                self.thumbnail_cache[profile] = image
                self.thumbnail_futures.pop(profile, None)
                return image
            return QImage()
        path = self.resolve_media_profile(profile)
        if profile == "Default3000925581Pkg":
            preview = self.workspace / "3000925581" / "preview.gif"
            path = preview if preview.exists() else None
        if path is not None and path.exists():
            self.thumbnail_futures[profile] = self.thumbnail_executor.submit(self._load_thumbnail, path)
        return QImage()

    @staticmethod
    def _load_thumbnail(path: Path) -> QImage:
        try:
            import cv2  # type: ignore

            capture = cv2.VideoCapture(str(path))
            ok, frame = capture.read()
            capture.release()
            if not ok:
                return QImage()
            height, width = frame.shape[:2]
            max_width = 480
            if width > max_width:
                ratio = max_width / float(width)
                frame = cv2.resize(frame, (max_width, max(2, round(height * ratio))), interpolation=cv2.INTER_AREA)
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            height, width, channels = rgb.shape
            return QImage(rgb.data, width, height, channels * width, QImage.Format.Format_RGB888).copy()
        except Exception:
            return QImage()

    def draw(
        self,
        p: QPainter,
        rect: QRectF,
        mouse: QPointF,
        dt: float,
        *,
        mode: str,
        profile: str,
        parallax: float,
        zoom: float,
        dim: float,
        high_res: bool,
        motion_quality: str = "Balanced30",
    ) -> bool:
        self.fast_background_sampling = bool(high_res)
        if profile != self.selected_profile:
            self.selected_profile = profile
            self.failed_media = None
        if mode == "ParticleGrid" or profile == "ParticleGrid":
            self.video_player.set_active(False)
            self.requested_media = None
            self.last_frame = QImage()
            self.last_draw_rect = QRectF()
            self.soft_glass_frame = QImage()
            self.glass_frame = QImage()
            return False
        media_path = self.resolve_media_profile(profile)
        if media_path is None and profile not in {"Default3000925581Pkg", "ParticleGrid"} and self.texas_video.exists():
            media_path = self.texas_video
        if media_path is not None or mode in ("VideoParallax", "Static"):
            if media_path is not None:
                resolved = media_path.resolve()
                if self.failed_media == resolved and resolved != self.texas_video.resolve():
                    resolved = self.texas_video.resolve()
                if self.requested_media != resolved:
                    self.transition_frame = self.last_frame
                    self.transition_started = time.perf_counter()
                    self.requested_media = resolved
                self.video_player.configure((round(rect.width()), round(rect.height())), motion_quality)
                self.video_player.set_active(True)
                loaded = self.video_player.load(resolved)
                if loaded:
                    snapshot = self.video_player.snapshot()
                    if snapshot.path == resolved and not snapshot.image.isNull():
                        if self.failed_media == resolved:
                            self.failed_media = None
                        self._update_glass_frame(snapshot, motion_quality)
                        return self._draw_image_parallax(p, rect, mouse, snapshot.image, dt, parallax, zoom, dim)
                    if self.video_player.last_error and resolved != self.texas_video.resolve():
                        self.failed_media = resolved
                    if not self.last_frame.isNull():
                        return self._draw_image_parallax(p, rect, mouse, self.last_frame, dt, parallax, zoom, dim)
                elif self.video_player.last_error and resolved != self.texas_video.resolve():
                    self.failed_media = resolved
        if profile == "Default3000925581Pkg" or mode == "PkgScene":
            self.video_player.set_active(False)
            self.requested_media = None
            if self.pkg_scene.draw(p, rect, mouse, dt, parallax, zoom, dim):
                image = self.pkg_scene._composed_scene_image()
                if not image.isNull():
                    self.last_frame = image
                    image_key = image.cacheKey()
                    if self.glass_frame.isNull() or image_key != self.static_glass_key:
                        self.soft_glass_frame = image.scaledToWidth(384, Qt.TransformationMode.SmoothTransformation)
                        self.glass_frame = self.soft_glass_frame
                        self.static_glass_key = image_key
                    image_scale = min(rect.width() / image.width(), rect.height() / image.height()) * max(1.0, min(1.18, zoom))
                    self.last_draw_rect = QRectF(
                        rect.center().x() - image.width() * image_scale / 2.0,
                        rect.center().y() - image.height() * image_scale / 2.0,
                        image.width() * image_scale,
                        image.height() * image_scale,
                    )
                return True
        return False

    def draw_glass_sample(
        self,
        p: QPainter,
        rect: QRectF,
        radius: float,
        scale: float = 1.0,
        *,
        surface: str = "MainGlass",
        strength: float = 1.0,
    ) -> bool:
        if self.last_frame.isNull() or self.last_draw_rect.isEmpty():
            return False
        if self.soft_glass_frame.isNull() or self.glass_frame.isNull():
            return False

        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        p.save()
        base_opacity = p.opacity()
        p.setClipPath(path)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)

        core_source = self._glass_source_rect(rect, self.soft_glass_frame)
        if core_source.isEmpty():
            p.restore()
            return False
        core_alpha = {"MainGlass": 0.42, "ModalGlass": 0.46, "SubGlass": 0.14}.get(surface, 0.38)
        p.setOpacity(base_opacity * core_alpha * strength)
        p.drawImage(rect, self.soft_glass_frame, core_source)

        # 旧镜片回退效果只在边缘窄带折射，中心保持清透。
        edge = max(2.0 * scale, min(8.0 * scale, 5.0 * scale * strength))
        inner_rect = rect.adjusted(edge, edge, -edge, -edge)
        if inner_rect.width() > 2.0 and inner_rect.height() > 2.0:
            inner_path = QPainterPath()
            inner_path.addRoundedRect(inner_rect, max(1.0, radius - edge), max(1.0, radius - edge))
            rim_path = path.subtracted(inner_path)
            lens_source = self._glass_source_rect(rect, self.glass_frame, -1.7 * scale * strength, 1.2 * scale * strength)
            if not lens_source.isEmpty():
                p.setClipPath(rim_path)
                p.setOpacity(base_opacity * 0.34 * strength)
                p.drawImage(rect, self.glass_frame, lens_source)
        p.restore()
        return True

    def _glass_source_rect(self, rect: QRectF, image: QImage, offset_x: float = 0.0, offset_y: float = 0.0) -> QRectF:
        source = QRectF(
            (rect.left() - self.last_draw_rect.left()) / max(1.0, self.last_draw_rect.width()) * image.width(),
            (rect.top() - self.last_draw_rect.top()) / max(1.0, self.last_draw_rect.height()) * image.height(),
            rect.width() / max(1.0, self.last_draw_rect.width()) * image.width(),
            rect.height() / max(1.0, self.last_draw_rect.height()) * image.height(),
        )
        return source.translated(offset_x, offset_y).intersected(QRectF(0, 0, image.width(), image.height()))

    def _update_glass_frame(self, snapshot: VideoFrameSnapshot, motion_quality: str) -> None:
        if snapshot.soft_glass.isNull() or snapshot.glass.isNull():
            return
        # 只接收完整发布的源帧快照；绝不在 UI 帧内重新混合纹理。
        if snapshot.sequence != self.glass_sequence:
            self.soft_glass_frame = snapshot.soft_glass
            self.glass_frame = snapshot.glass
            self.glass_sequence = snapshot.sequence

    def prepare_gpu_video(
        self,
        rect: QRectF,
        mouse: QPointF,
        dt: float,
        *,
        mode: str,
        profile: str,
        parallax: float,
        zoom: float,
        high_res: bool,
        motion_quality: str,
    ) -> VideoFrameSnapshot | None:
        """准备 OpenGL 全屏视频纹理；粒子和 PKG 场景仍走原有 QPainter 路径。"""
        self.fast_background_sampling = bool(high_res)
        if mode in ("ParticleGrid", "PkgScene") or profile in ("ParticleGrid", "Default3000925581Pkg"):
            return None
        if profile != self.selected_profile:
            self.selected_profile = profile
            self.failed_media = None
        media_path = self.resolve_media_profile(profile)
        if media_path is None and self.texas_video.exists():
            media_path = self.texas_video
        if media_path is None:
            return None
        resolved = media_path.resolve()
        if self.requested_media != resolved:
            self.requested_media = resolved
            self.transition_frame = self.last_frame
            self.transition_started = time.perf_counter()
        self.video_player.configure((round(rect.width()), round(rect.height())), motion_quality)
        self.video_player.set_active(True)
        if not self.video_player.load(resolved):
            return None
        snapshot = self.video_player.snapshot()
        if snapshot.path != resolved or snapshot.image.isNull():
            return None
        self._update_glass_frame(snapshot, motion_quality)
        nx = (mouse.x() / max(1.0, rect.width()) - 0.5) * 2.0
        ny = (mouse.y() / max(1.0, rect.height()) - 0.5) * 2.0
        target_x = -nx * parallax
        target_y = -ny * parallax * 0.62
        smooth = 1.0 - math.exp(-dt * 9.0)
        self.parallax_x += (target_x - self.parallax_x) * smooth
        self.parallax_y += (target_y - self.parallax_y) * smooth
        image_aspect = snapshot.image.width() / max(1, snapshot.image.height())
        safety_x = abs(parallax) + 8.0
        safety_y = abs(parallax) * 0.62 + 8.0
        safe_zoom = max(
            1.0 + safety_x * 2.0 / max(1.0, rect.width()),
            1.0 + safety_y * 2.0 / max(1.0, rect.height()),
        )
        frame_zoom = max(safe_zoom, min(1.35, max(1.0, zoom)))
        target_w = rect.width() * frame_zoom
        target_h = rect.height() * frame_zoom
        if image_aspect > target_w / max(1.0, target_h):
            draw_h = target_h
            draw_w = draw_h * image_aspect
        else:
            draw_w = target_w
            draw_h = draw_w / max(0.01, image_aspect)
        self.last_frame = snapshot.image
        self.last_draw_rect = QRectF(
            rect.center().x() - draw_w / 2.0 + self.parallax_x,
            rect.center().y() - draw_h / 2.0 + self.parallax_y,
            draw_w,
            draw_h,
        )
        return snapshot

    def _draw_image_parallax(
        self,
        p: QPainter,
        rect: QRectF,
        mouse: QPointF,
        image: QImage,
        dt: float,
        parallax: float,
        zoom: float,
        dim: float,
    ) -> bool:
        if image.isNull():
            return False
        nx = (mouse.x() / max(1.0, rect.width()) - 0.5) * 2.0
        ny = (mouse.y() / max(1.0, rect.height()) - 0.5) * 2.0
        target_x = -nx * parallax
        target_y = -ny * parallax * 0.62
        smooth = 1.0 - math.exp(-dt * 9.0)
        self.parallax_x += (target_x - self.parallax_x) * smooth
        self.parallax_y += (target_y - self.parallax_y) * smooth
        iw = image.width()
        ih = image.height()
        image_aspect = iw / max(1, ih)
        # cover 本身可能刚好贴边；把最大视差换算为额外 zoom，鼠标移动到边缘也不会露底。
        safety_x = abs(parallax) + 8.0
        safety_y = abs(parallax) * 0.62 + 8.0
        safe_zoom = max(
            1.0 + safety_x * 2.0 / max(1.0, rect.width()),
            1.0 + safety_y * 2.0 / max(1.0, rect.height()),
        )
        frame_zoom = max(safe_zoom, min(1.35, max(1.0, zoom)))
        target_w = rect.width() * frame_zoom
        target_h = rect.height() * frame_zoom
        if image_aspect > target_w / max(1.0, target_h):
            draw_h = target_h
            draw_w = draw_h * image_aspect
        else:
            draw_w = target_w
            draw_h = draw_w / max(0.01, image_aspect)
        draw_rect = QRectF(rect.center().x() - draw_w / 2.0 + self.parallax_x, rect.center().y() - draw_h / 2.0 + self.parallax_y, draw_w, draw_h)
        self.last_frame = image
        self.last_draw_rect = QRectF(draw_rect)
        p.save()
        p.setClipRect(rect)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, not self.fast_background_sampling)
        transition = 1.0
        if not self.transition_frame.isNull():
            transition = max(0.0, min(1.0, (time.perf_counter() - self.transition_started) / 0.26))
            if transition < 1.0:
                p.setOpacity(1.0 - transition)
                p.drawImage(draw_rect, self.transition_frame)
            else:
                self.transition_frame = QImage()
        p.setOpacity(transition)
        p.drawImage(draw_rect, image)
        p.setOpacity(1.0)
        # 启动页只做很轻的整体收敛，镜片自身不再靠黑幕获得对比度。
        p.fillRect(rect, QColor(8, 14, 20, int(10 + dim * 42)))
        p.restore()
        return True
