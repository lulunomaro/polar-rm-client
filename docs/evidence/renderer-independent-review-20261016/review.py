from pathlib import Path
import sys, unittest
from unittest.mock import Mock
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from tests import test_compositor_initialization_ownership as fixtures

class IndependentOwnerReview(unittest.TestCase):
    def test_each_failed_owner_blocks_replacement_until_exact_owner_released(self):
        for name in ('present_program', 'blur_program', 'jello_blur_program', 'glass_program', 'vertex_buffer', 'vertex_array'):
            with self.subTest(owner=name):
                fixture=fixtures.CompositorInitializationOwnershipTests(); fixture.make_compositor(); c=fixture.c
                self.assertTrue(c.initialize())
                owner=getattr(c,name)
                method=owner.destroy if name.startswith('vertex_') else owner.removeAllShaders
                prior=method.side_effect
                method.side_effect=OSError('retained '+name)
                with self.assertRaises(RuntimeError): c.release()
                self.assertTrue(c._release_pending)
                self.assertFalse(c.initialize())
                self.assertIs(getattr(c,name),owner)
                for builder in fixture.builders: builder.assert_called_once()
                method.side_effect=prior
                self.assertTrue(c.initialize())
                self.assertFalse(c._release_pending)
                for builder in fixture.builders: self.assertEqual(builder.call_count,2)
                c.release()

    def test_setup_none_and_false_have_distinct_meanings(self):
        for result in (None,False):
            with self.subTest(result=result):
                fixture=fixtures.CompositorInitializationOwnershipTests(); fixture.make_compositor(); c=fixture.c
                c.vertex_buffer.release.return_value=result
                c.vertex_array.release.return_value=None
                self.assertIs(c.initialize(),result is None)
                self.assertEqual(c.diagnostics.faults,0)
                if result is False:
                    self.assertIsNone(c.present_program)
                    c.vertex_buffer.release.return_value=None
                    self.assertTrue(c.initialize())
                c.release()

if __name__=='__main__': unittest.main()
