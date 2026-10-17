"""Unsupported Qt buttons must never widen into a supported control intent.

Real Qt events, KeyboardControlBridge and OfficialSafety; accepted egress is an
inert recorder. No network, serial writer or physical robot is constructed.
"""
from dataclasses import replace
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'tests'))
from PySide6.QtCore import QEvent, QPointF, QRectF, Qt
from PySide6.QtGui import QMouseEvent
from rm_client.backend.safety import OfficialSafety
from rm_client.control import KeyboardControlBridge
from rm_client.model import RobotState
from rm_client.ui.polar.mixins import input_dispatch_mixin as route_module
from test_pointer_routing_characterization import Case, Host

SUPPORTED=(Qt.MouseButton.LeftButton,Qt.MouseButton.MiddleButton,Qt.MouseButton.RightButton)
UNSUPPORTED=(Qt.MouseButton.NoButton,Qt.MouseButton.BackButton,Qt.MouseButton.ForwardButton,
             Qt.MouseButton.TaskButton,Qt.MouseButton.ExtraButton24,
             Qt.MouseButton.LeftButton|Qt.MouseButton.RightButton)


class Sink:
    def __init__(self,armed=True):
        self.safety=OfficialSafety()
        self.safety.configure(mock_writes=False,allow_mqtt_publish=True,enforce_official_limits=True)
        self.safety.update_stage(4)
        if armed:self.safety.arm_match(['core_kmc'])
        self.now=1.
        self.attempts=[];self.frames=[]
    def publish_keyboard_mouse(self,*motion,**buttons):
        self.now+=1.
        self.attempts.append((motion,buttons))
        ok,reason=self.safety.can_publish('KeyboardMouseControl',now=self.now)
        if ok:
            self.safety.mark_published('KeyboardMouseControl',now=self.now)
            self.frames.append((motion,buttons))
        return ok,reason


class ControlHost(Host):
    def __init__(self,*,gui=False,armed=True):
        super().__init__(Case(gui=gui,hits=()),None)
        self.sink=Sink(armed)
        self.control=KeyboardControlBridge(publisher=self.sink,mock_output=False)
        self.backend.mqtt.mock_output=False
        self.state=RobotState()
        self.registry=NS(get=lambda name:None)


def event(button,pressed):
    kind=QEvent.Type.MouseButtonPress if pressed else QEvent.Type.MouseButtonRelease
    ev=QMouseEvent(kind,QPointF(10,20),QPointF(10,20),button,
                   button if pressed else Qt.MouseButton.NoButton,Qt.KeyboardModifier.NoModifier)
    ev.setAccepted(False)
    return ev


def route(host,button,pressed):
    ev=event(button,pressed)
    (host.mousePressEvent if pressed else host.mouseReleaseEvent)(ev)
    return ev


class MouseButtonAdmissionTests(unittest.TestCase):
    def test_known_buttons_reach_real_armed_bridge_and_matching_release(self):
        fields=('left','mid','right')
        for button,field in zip(SUPPORTED,fields):
            with self.subTest(button=button):
                host=ControlHost()
                route(host,button,True);route(host,button,False)
                self.assertEqual(len(host.sink.frames),2)
                self.assertTrue(host.sink.frames[0][1][field])
                self.assertFalse(any(host.sink.frames[1][1][name] for name in fields))
                self.assertEqual(host.control.seq,2)

    def test_unknown_buttons_never_mutate_or_attempt_egress(self):
        for button in UNSUPPORTED:
            with self.subTest(button=button):
                host=ControlHost()
                self.assertTrue(route(host,button,True).isAccepted())
                self.assertTrue(route(host,button,False).isAccepted())
                self.assertEqual(host.sink.attempts,[])
                self.assertEqual(host.control.seq,0)
                self.assertFalse(host.control.left_button_down)
                self.assertFalse(host.control.mid_button_down)
                self.assertFalse(host.control.right_button_down)

    def test_unknown_release_cannot_release_a_held_left_command(self):
        for button in UNSUPPORTED:
            with self.subTest(button=button):
                host=ControlHost()
                route(host,Qt.MouseButton.LeftButton,True)
                route(host,button,False)
                self.assertTrue(host.control.left_button_down)
                self.assertEqual(len(host.sink.frames),1)
                route(host,Qt.MouseButton.LeftButton,False)
                self.assertFalse(host.control.left_button_down)
                self.assertEqual(len(host.sink.frames),2)

    def test_unknown_press_never_dispatches_a_button_agnostic_hotspot(self):
        for button in UNSUPPORTED:
            host=ControlHost(gui=True)
            host.hotspots=[NS(rect=QRectF(0,0,50,50),button=None,action='setting_toggle',payload=None)]
            route(host,button,True)
            self.assertFalse(any(row[0]=='dispatch' for row in host.trace))

    def test_unknown_events_preserve_existing_capture_and_background_dismissal(self):
        for button in UNSUPPORTED:
            host=ControlHost(gui=True)
            drag={'type':'slider'}
            host.pointer_interaction.drag=drag
            host.pointer_interaction.own_background_press(QPointF(10,20))
            route(host,button,True);route(host,button,False)
            self.assertIs(host.pointer_interaction.drag,drag)
            self.assertEqual(host.pointer_interaction.background_press,QPointF(10,20))
            self.assertTrue(host.pointer_interaction.background_release_owned)

    def test_epoch_cancelled_left_release_is_not_consumed_by_extra_button(self):
        host=ControlHost(gui=True)
        host.pointer_interaction.drag={'type':'slider'}
        host.pointer_interaction.cancel_epoch()
        route(host,Qt.MouseButton.BackButton,False)
        self.assertTrue(host.pointer_interaction.background_release_owned)
        route(host,Qt.MouseButton.LeftButton,False)
        self.assertFalse(host.pointer_interaction.background_release_owned)
        self.assertIsNone(host.pointer_interaction.drag)
        self.assertEqual(host.sink.attempts,[])

    def test_left_fallback_mutant_produces_detectable_authorized_egress(self):
        def unsafe_fallback(event):
            return route_module._QT_POINTER_BUTTONS.get(event.button(),1)
        host=ControlHost()
        with patch.object(route_module,'_pointer_button',unsafe_fallback):
            route(host,Qt.MouseButton.BackButton,True)
        self.assertEqual(len(host.sink.frames),1)
        self.assertTrue(host.sink.frames[0][1]['left'])

    def test_existing_lifecycle_and_backend_lock_still_deny_known_intents(self):
        host=ControlHost()
        host.case=replace(host.case,lifecycle=True)
        with patch.object(route_module,'_pointer_button',side_effect=AssertionError('lifecycle must own first')):
            route(host,Qt.MouseButton.LeftButton,True)
            route(host,Qt.MouseButton.BackButton,False)
        self.assertEqual(host.sink.attempts,[])
        locked=ControlHost(armed=False)
        route(locked,Qt.MouseButton.LeftButton,True)
        self.assertEqual(len(locked.sink.attempts),1)
        self.assertEqual(locked.sink.frames,[])
        self.assertIn('LOCKED',locked.control.last_error)

if __name__=='__main__':unittest.main()
