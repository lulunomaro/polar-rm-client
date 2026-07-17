from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Callable


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


@dataclass(frozen=True)
class MotionSpec:
    """统一动效语义：hover / open / page 不应共用同一套 pow4。"""

    duration: float
    easing: Callable[[float], float]


class Easings:
    @staticmethod
    def linear(t: float) -> float:
        return clamp01(t)

    @staticmethod
    def ease_out_quad(t: float) -> float:
        t = clamp01(t)
        return 1.0 - (1.0 - t) * (1.0 - t)

    @staticmethod
    def ease_out_cubic(t: float) -> float:
        t = clamp01(t)
        return 1.0 - pow(1.0 - t, 3.0)

    @staticmethod
    def ease_out_pow3(t: float) -> float:
        return Easings.ease_out_cubic(t)

    @staticmethod
    def ease_in_pow3(t: float) -> float:
        t = clamp01(t)
        return t * t * t

    @staticmethod
    def ease_out_pow4(t: float) -> float:
        t = clamp01(t)
        return 1.0 - pow(1.0 - t, 4.0)

    @staticmethod
    def ease_out_pow5(t: float) -> float:
        t = clamp01(t)
        return 1.0 - pow(1.0 - t, 5.0)

    @staticmethod
    def ease_out_expo(t: float) -> float:
        t = clamp01(t)
        if t >= 1.0:
            return 1.0
        return 1.0 - pow(2.0, -10.0 * t)

    @staticmethod
    def ease_out_sine(t: float) -> float:
        return math.sin(clamp01(t) * math.pi / 2.0)

    @staticmethod
    def back_out(t: float) -> float:
        t = clamp01(t) - 1.0
        c1 = 1.70158
        c3 = c1 + 1.0
        return 1.0 + c3 * t * t * t + c1 * t * t

    @staticmethod
    def soft_back_out(t: float) -> float:
        """比 back_out 更克制的回弹，适合页面入场。"""
        t = clamp01(t) - 1.0
        c1 = 1.12
        c3 = c1 + 1.0
        return 1.0 + c3 * t * t * t + c1 * t * t


class MotionTokens:
    """启动页 / ClickGUI 共用的三套动效 token。"""

    HOVER = MotionSpec(0.11, Easings.ease_out_quad)
    OPEN = MotionSpec(0.26, Easings.ease_out_cubic)
    PAGE = MotionSpec(0.32, Easings.soft_back_out)
    PRESS = MotionSpec(0.08, Easings.ease_out_quad)
    PARALLAX = MotionSpec(0.14, Easings.ease_out_sine)
    STAGGER_DELAY = 0.055
    STAGGER_DURATION = 0.34
    PAGE_ENTER_FROM = 0.22


@dataclass
class SmoothAnimation:
    value: float = 0.0
    from_value: float = 0.0
    to_value: float = 0.0
    duration: float = 0.2
    start_time: float = 0.0
    easing: Callable[[float], float] = Easings.ease_out_quad

    def animate(self, target: float, duration: float = 0.2, easing: Callable[[float], float] = Easings.ease_out_quad) -> None:
        if abs(target - self.to_value) < 0.0001 and not self.done:
            return
        if abs(target - self.value) < 0.0001 and self.done:
            self.to_value = target
            self.value = target
            return
        self.from_value = self.value
        self.to_value = target
        self.duration = max(0.001, duration)
        self.start_time = time.perf_counter()
        self.easing = easing

    @property
    def done(self) -> bool:
        return time.perf_counter() - self.start_time >= self.duration

    def tick(self) -> float:
        t = (time.perf_counter() - self.start_time) / self.duration
        if t >= 1.0:
            self.value = self.to_value
            return self.value
        eased = self.easing(t)
        self.value = self.from_value + (self.to_value - self.from_value) * eased
        return self.value

    def reset(self, value: float) -> None:
        self.value = value
        self.from_value = value
        self.to_value = value
        self.start_time = time.perf_counter() - self.duration


@dataclass
class SpringAnimation:
    stiffness: float
    mass: float
    damping: float
    value: float
    target: float | None = None
    velocity: float = 0.0

    def __post_init__(self) -> None:
        if self.target is None:
            self.target = self.value

    def set_target(self, target: float) -> None:
        self.target = target

    def reset(self, value: float) -> None:
        self.value = value
        self.target = value
        self.velocity = 0.0

    def update(self, dt: float) -> float:
        dt = max(0.0, min(0.033, dt))
        force = -self.stiffness * (self.value - float(self.target)) - self.damping * self.velocity
        acceleration = force / self.mass
        self.velocity += acceleration * dt
        self.value += self.velocity * dt
        return self.value


class AnimationBank:
    def __init__(self) -> None:
        self._smooth: dict[str, SmoothAnimation] = {}
        self._springs: dict[str, SpringAnimation] = {}

    def value(
        self,
        key: str,
        target: float,
        duration: float = 0.2,
        easing: Callable[[float], float] = Easings.ease_out_quad,
        initial: float | None = None,
    ) -> float:
        anim = self._smooth.get(key)
        if anim is None:
            start = target if initial is None else initial
            anim = SmoothAnimation(value=start, from_value=start, to_value=start, duration=duration)
            self._smooth[key] = anim
        anim.animate(target, duration, easing)
        return anim.tick()

    def motion(
        self,
        key: str,
        target: float,
        spec: MotionSpec,
        *,
        initial: float | None = None,
        duration_scale: float = 1.0,
    ) -> float:
        duration = max(0.01, spec.duration * max(0.2, duration_scale))
        return self.value(key, target, duration, spec.easing, initial=initial)

    def spring(
        self,
        key: str,
        target: float,
        dt: float,
        *,
        stiffness: float = 210.0,
        damping: float = 22.0,
        mass: float = 1.0,
        initial: float | None = None,
    ) -> float:
        anim = self._springs.get(key)
        if anim is None:
            start = target if initial is None else initial
            anim = SpringAnimation(stiffness=stiffness, mass=mass, damping=damping, value=start, target=start)
            self._springs[key] = anim
        anim.set_target(target)
        return anim.update(dt)

    def reset(self, key: str, value: float = 0.0) -> None:
        anim = self._smooth.get(key)
        if anim is not None:
            anim.reset(value)
        spring = self._springs.get(key)
        if spring is not None:
            spring.reset(value)

    def reset_prefix(self, prefix: str, value: float = 0.0) -> None:
        for key, anim in self._smooth.items():
            if key.startswith(prefix):
                anim.reset(value)
        for key, spring in self._springs.items():
            if key.startswith(prefix):
                spring.reset(value)
