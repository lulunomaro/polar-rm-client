"""Compatibility launch path for callers predating the explicit Qt boundary.

Production ``polar_app`` depends directly on ``qt_application`` and does not
import this wrapper. Importing this module alone does not load the window.
"""
from __future__ import annotations

from .qt_application import _retain_window_until_close, launch_qt_application


def main() -> None:
    from ..polar_app import PolarRMWindow

    launch_qt_application(PolarRMWindow)
