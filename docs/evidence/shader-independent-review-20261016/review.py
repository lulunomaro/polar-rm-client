"""Independent multi-owner compile failure/retry and private carrier checks."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from rm_client.ui import gpu_frame_compositor as gpu
from tests import test_compositor_shader_ownership as fixtures


class IndependentShaderReview(unittest.TestCase):
    def test_two_failed_owners_remain_independent_through_partial_repair(self):
        fixture = fixtures.CompositorShaderOwnershipTests()
        for slot in (1, 2, 3):
            with self.subTest(slot=slot):
                compositor = fixture.compositor()
                programs = [fixture.program() for _ in range(slot + 1)]
                prior, candidate = programs[0], programs[-1]
                root = TypeError('original compile error')
                candidate.link.side_effect = root
                prior.removeAllShaders.side_effect = OSError('prior retained')
                candidate.removeAllShaders.side_effect = ValueError('candidate retained')
                with patch.object(gpu, 'QOpenGLShaderProgram', side_effect=programs) as factory, patch.object(gpu, '_LOG'):
                    self.assertFalse(compositor.initialize())
                    initial = compositor.last_failed_frame
                    self.assertEqual(initial.render_fault.message, str(root))
                    self.assertEqual([fault.message for fault in initial.cleanup_faults],
                                     ['prior retained', 'candidate retained'])
                    for program in programs:
                        program.removeAllShaders.assert_called_once()
                    self.assertIs(compositor.present_program, prior)
                    self.assertIs(getattr(compositor, fixtures.SLOTS[slot]), candidate)
                    prior.removeAllShaders.side_effect = None
                    self.assertFalse(compositor.initialize())
                    self.assertIsNone(compositor.present_program)
                    self.assertIs(getattr(compositor, fixtures.SLOTS[slot]), candidate)
                    self.assertEqual(factory.call_count, slot + 1)
                    self.assertEqual(initial.render_fault.message, str(root))
                candidate.removeAllShaders.side_effect = None
                replacements = [fixture.program() for _ in range(4)]
                with patch.object(gpu, 'QOpenGLShaderProgram', side_effect=replacements):
                    self.assertTrue(compositor.initialize())
                self.assertEqual(prior.removeAllShaders.call_count, 2)
                self.assertEqual(candidate.removeAllShaders.call_count, 3)
                compositor.release()

    def test_private_carrier_explicitly_preserves_original_cause_and_candidate(self):
        fixture = fixtures.CompositorShaderOwnershipTests()
        program = fixture.program()
        root = TypeError('compile rejected')
        program.link.side_effect = root
        with patch.object(gpu, 'QOpenGLShaderProgram', return_value=program):
            with self.assertRaises(gpu._GlProgramBuildFailure) as raised:
                gpu.GpuFrameCompositor._compile('vertex', 'fragment')
        self.assertIs(raised.exception.program, program)
        self.assertIs(raised.exception.__cause__, root)
        self.assertIs(raised.exception.failures[0][1], root)
        program.removeAllShaders.assert_not_called()
        # A direct private caller, unlike initialize(), owns this retirement.
        raised.exception.program.removeAllShaders()


if __name__ == '__main__':
    unittest.main()
