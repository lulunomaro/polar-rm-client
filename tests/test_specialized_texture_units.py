"""Startup texture-unwind state using Qt's documented active-unit contract.

Native GL pixels are outside this suite. The fake models QOpenGLTexture.bind(unit)
selecting that unit and release() affecting only the current active unit.
"""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.test_specialized_gl_owners import StartupGpuCompositor, make_renderer


class TextureUnits:
    def __init__(self):
        self.active = 5
        self.bindings = {}


class Texture:
    def __init__(self, state, name, *, bind_error=None, release_error=None):
        self.state = state
        self.name = name
        self.bind_error = bind_error
        self.release_error = release_error
        self.released_units = []

    def bind(self, unit):
        self.state.active = unit
        self.state.bindings[unit] = self.name
        if self.bind_error:
            raise self.bind_error

    def release(self, unit=None):
        if unit is not None:
            self.state.active = unit
        self.released_units.append(self.state.active)
        if self.release_error:
            raise self.release_error
        self.state.bindings[self.state.active] = None


class SpecializedTextureUnitTests(unittest.TestCase):
    def renderer(self, *, previous=True):
        renderer, functions, buffer, render, trace = make_renderer(
            StartupGpuCompositor, previous=previous)
        units = TextureUnits()
        renderer.current = Texture(units, 'current')
        renderer.previous = Texture(units, 'previous') if previous else None
        return renderer, functions, render, units

    def assert_released(self, renderer, units, *, previous=True):
        self.assertIsNone(units.bindings[0])
        self.assertEqual(renderer.current.released_units, [0])
        if previous:
            self.assertIsNone(units.bindings[1])
            self.assertEqual(renderer.previous.released_units, [1])
        self.assertEqual(units.active, 0)

    def test_success_clears_both_exact_bound_units(self):
        renderer, functions, render, units = self.renderer()
        self.assertTrue(render())
        functions.glDrawArrays.assert_called_once()
        self.assert_released(renderer, units)

    def test_single_texture_clears_its_unit_without_inventing_previous_binding(self):
        renderer, functions, render, units = self.renderer(previous=False)
        self.assertTrue(render())
        self.assert_released(renderer, units, previous=False)
        self.assertNotIn(1, units.bindings)

    def test_draw_fault_still_releases_both_units_and_preserves_primary(self):
        renderer, functions, render, units = self.renderer()
        error = TypeError('draw failed')
        functions.glDrawArrays.side_effect = error
        self.assertFalse(render())
        self.assert_released(renderer, units)
        self.assertEqual(renderer.resources.last_result.primary_fault.error_type, 'TypeError')

    def test_partial_previous_bind_releases_its_unit_and_current(self):
        renderer, functions, render, units = self.renderer()
        renderer.previous.bind_error = TypeError('failed after binding previous')
        self.assertFalse(render())
        functions.glDrawArrays.assert_not_called()
        self.assert_released(renderer, units)

    def test_failed_previous_release_does_not_skip_current_unit(self):
        renderer, functions, render, units = self.renderer()
        renderer.previous.release_error = OSError('unit1 driver release failed')
        self.assertFalse(render())
        self.assertEqual(units.bindings[1], 'previous')
        self.assertIsNone(units.bindings[0])
        self.assertEqual(renderer.previous.released_units, [1])
        self.assertEqual(renderer.current.released_units, [0])
        faults = renderer.resources.last_result.cleanup_faults
        self.assertEqual(len(faults), 1)
        self.assertEqual(faults[0].error_type, 'OSError')
        self.assertIn('texture.1', faults[0].phase)
        renderer.program.release.assert_called_once()


if __name__ == '__main__':
    unittest.main()
