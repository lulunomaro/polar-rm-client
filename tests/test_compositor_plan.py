"""Truth tables and pass ordering for frame plans, without an OpenGL context."""
from itertools import product
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rm_client.ui.gpu_frame_compositor import GlassOwner
from rm_client.ui.polar.compositor_plan import (
    BackdropTreatment, FinalFrameCompositor, FrameCompositionPlan,
    OverlayScene, motion_layer_needs_refresh,
)


class OverlaySceneTests(unittest.TestCase):
    def test_startup_owns_all_modal_layers_regardless_of_stale_visibility(self):
        scene = OverlayScene.resolve(startup_scene=True, economy_visible=True, economy_progress=1., clickgui_visible=True, clickgui_progress=1., student_visible=True, escape_visible=True)
        self.assertEqual(scene, OverlayScene(False, False, False, False))
        self.assertFalse(scene.modal_backdrop_active)

    def test_modal_visibility_and_fade_thresholds(self):
        for visible, progress, expected in ((False, .01, False), (False, .011, True), (True, 0., True), (False, -1., False)):
            scene = OverlayScene.resolve(startup_scene=False, economy_visible=visible, economy_progress=progress, clickgui_visible=visible, clickgui_progress=progress, student_visible=False, escape_visible=False)
            self.assertEqual(scene.economy_active, expected)
            self.assertEqual(scene.clickgui_active, expected)
            self.assertEqual(scene.modal_backdrop_active, expected)

    def test_glass_owner_selection_never_adds_hud_to_overlay(self):
        for economy, clickgui, radial, hero in product((False, True), repeat=4):
            scene = OverlayScene(economy, clickgui, True, True)
            owners = scene.glass_owners(radial_visible=radial, hero_visible=hero)
            self.assertEqual(owners, frozenset(owner for enabled, owner in ((economy, GlassOwner.ECONOMY), (clickgui, GlassOwner.CLICKGUI), (radial, GlassOwner.RADIAL), (hero, GlassOwner.HERO_DEPLOY)) if enabled))
            self.assertNotIn(GlassOwner.HUD, owners)


class BackdropTests(unittest.TestCase):
    def treatment(self, scene, **changes):
        values = dict(economy_progress=.5, clickgui_progress=.4, jello_blur_radius=None, student_panel="damage", student_progress=.2)
        values.update(changes)
        return BackdropTreatment.resolve(scene, **values)

    def test_economy_wins_over_every_other_modal_and_excludes_overlay(self):
        actual = self.treatment(OverlayScene(True, True, True, True), jello_blur_radius=20.)
        self.assertEqual(actual, BackdropTreatment(True, .5, 2., .40, False, "compact_gaussian"))

    def test_jello_blur_uses_timeline_and_no_second_dim_layer(self):
        actual = self.treatment(OverlayScene(False, True, True, True), jello_blur_radius=17.5)
        self.assertEqual(actual, BackdropTreatment(True, 1., 17.5, 0., True, "jello_box20"))

    def test_escape_precedes_student_and_legacy(self):
        actual = self.treatment(OverlayScene(False, True, True, True))
        self.assertEqual(actual, BackdropTreatment(True, 1., 20., 0., True, "jello_box20"))

    def test_student_damage_is_unblurred_and_other_panels_follow_reveal(self):
        scene = OverlayScene(False, False, True, False)
        self.assertEqual(self.treatment(scene).blur_radius_px, 0.)
        self.assertEqual(self.treatment(scene, student_panel="team").blur_radius_px, 16.)
        self.assertEqual(self.treatment(scene, student_panel="team", student_progress=1.).blur_radius_px, 20.)

    def test_legacy_treatment_retains_original_progress_blur_and_dim(self):
        actual = self.treatment(OverlayScene(False, True, False, False))
        self.assertEqual(actual, BackdropTreatment(True, .4, 3., .26, True, "compact_gaussian"))

    def test_reveal_is_clamped_but_visible_zero_progress_stays_inactive(self):
        scene = OverlayScene(True, False, False, False)
        self.assertEqual(self.treatment(scene, economy_progress=2.).progress, 1.)
        self.assertEqual(self.treatment(scene, economy_progress=-.1).progress, 0.)
        self.assertFalse(self.treatment(scene, economy_progress=0.).active)
        scene = OverlayScene(False, True, False, False)
        self.assertFalse(self.treatment(scene, clickgui_progress=0., jello_blur_radius=0.).active)

    def test_application_passes_exact_shader_parameters(self):
        compositor = NS(set_backdrop_treatment=Mock(return_value=True))
        treatment = self.treatment(OverlayScene(False, True, False, False))
        self.assertTrue(treatment.apply(compositor))
        compositor.set_backdrop_treatment.assert_called_once_with(active=True, progress=.4, blur_radius_px=3., dim_alpha=.26, include_overlay=True, blur_kernel="compact_gaussian")


class MotionLayerPolicyTests(unittest.TestCase):
    def refresh(self, **changes):
        values = dict(radial_visible=False, chat_open=False, student_visible=False, escape_visible=False, ready=True, now=10., updated_at=9.9)
        values.update(changes)
        return motion_layer_needs_refresh(**values)

    def test_uncovered_hud_refreshes_each_frame(self):
        self.assertTrue(self.refresh())

    def test_radial_freezes_base_even_with_chat_or_escape(self):
        self.assertFalse(self.refresh(radial_visible=True, chat_open=True, escape_visible=True, updated_at=0.))
        self.assertTrue(self.refresh(radial_visible=True, ready=False))

    def test_chat_and_escape_refresh_at_exact_quarter_second(self):
        for modal in ("chat_open", "escape_visible"):
            self.assertFalse(self.refresh(**{modal: True}, updated_at=9.751))
            self.assertTrue(self.refresh(**{modal: True}, updated_at=9.75))

    def test_student_alone_freezes_base_but_new_storage_is_never_reused(self):
        self.assertFalse(self.refresh(student_visible=True, updated_at=0.))
        self.assertTrue(self.refresh(student_visible=True, ready=False))


class FinalCompositionTests(unittest.TestCase):
    def make_plan(self, **changes):
        values = dict(background_changed=False, control_changed=False, treatment_changed=False, static_ui_rebuilt=False, overlay_owners=frozenset({GlassOwner.CLICKGUI}), target_framebuffer=7)
        values.update(changes)
        return FrameCompositionPlan(**values)

    def compositor(self, failed=None):
        self.calls = []
        def record(name):
            def call(*args, **kwargs):
                step = name if name != "glass" else kwargs["destination"]
                self.calls.append((step, args, kwargs))
                return step != failed
            return call
        return NS(prepare_blur=record("blur"), render_glass=record("glass"), prepare_treated_backdrop=record("treated"), present=record("present"))

    def test_complete_pass_order_and_separate_glass_owners(self):
        functions = object()
        self.assertTrue(FinalFrameCompositor(self.compositor()).present(functions, self.make_plan()))
        self.assertEqual([call[0] for call in self.calls], ["blur", "hud", "treated", "overlay", "present"])
        self.assertEqual(self.calls[1][2], dict(include_static=True, owners={GlassOwner.HUD}, destination="hud", apply_backdrop_treatment=False))
        self.assertEqual(self.calls[3][2], dict(include_static=True, owners={GlassOwner.CLICKGUI}, destination="overlay"))
        self.assertEqual(self.calls[-1][1], (functions, 7))

    def test_rejected_pass_never_submits_later_passes_or_frame(self):
        steps = ["blur", "hud", "treated", "overlay", "present"]
        for index, failed in enumerate(steps):
            with self.subTest(failed=failed):
                self.assertFalse(FinalFrameCompositor(self.compositor(failed)).present(object(), self.make_plan()))
                self.assertEqual([call[0] for call in self.calls], steps[:index + 1])

    def test_dirty_flags_preserve_media_vs_modal_separation(self):
        for field in ("background_changed", "control_changed", "treatment_changed", "static_ui_rebuilt"):
            plan = self.make_plan(**{field: True})
            self.assertEqual(plan.base_blur_dirty, field in ("background_changed", "control_changed"))
            self.assertTrue(plan.treated_backdrop_dirty)
            FinalFrameCompositor(self.compositor()).present(object(), plan)
            self.assertEqual(self.calls[0][2]["background_changed"], plan.base_blur_dirty)
            self.assertTrue(self.calls[2][2]["background_changed"])

    def test_exceptions_reach_the_window_recovery_boundary(self):
        compositor = self.compositor()
        compositor.prepare_treated_backdrop = Mock(side_effect=TypeError("bad treatment"))
        with self.assertRaisesRegex(TypeError, "bad treatment"):
            FinalFrameCompositor(compositor).present(object(), self.make_plan())
        self.assertEqual([call[0] for call in self.calls], ["blur", "hud"])


if __name__ == "__main__":
    unittest.main()
