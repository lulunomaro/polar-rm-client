"""Synthetic completion races; no real camera or network is claimed."""
import unittest
from unittest.mock import patch
import cv2
from test_sim_platform_v2 import Client,state,frame
from rm_client.sim_platform_transport import PlatformTransport

class CompletionTests(unittest.TestCase):
    def setUp(self):
        self.c=Client();self.t=PlatformTransport(role='operator',clock=lambda:100.,client=self.c)
        self.t.connect_once();self.t.install_state(state());self.t.watch('red_infantry');self.t.process_jobs();self.t.install_state(state());self.t.install_frame(frame(self.t),generation=self.t._watch_generation)
    def test_stop_clears_all_display(self):
        self.t.stop();self.assertIsNone(self.t.snapshot()[0]);self.assertIsNone(self.t.snapshot()[1]);self.assertIsNone(self.t.raw_state);self.assertEqual(self.t.robot_ids,())
    def test_late_state_after_failure_does_not_clear_error(self):
        gen=self.t._connection_generation;self.t._failed(ValueError('disconnect'))
        self.assertFalse(self.t.install_state(state(),connection_generation=gen));self.assertFalse(self.t.install_state(state()));self.assertIsNone(self.t.telemetry);self.assertEqual(self.t.last_error,'disconnect')
    def test_late_decode_after_failure_is_discarded(self):
        real=cv2.imdecode
        def decode(*a,**kw):
            value=real(*a,**kw);self.t._failed(ValueError('disconnect'));return value
        with patch('cv2.imdecode',side_effect=decode):self.assertFalse(self.t.install_frame(frame(self.t,seq=2),generation=self.t._watch_generation))
        self.assertIsNone(self.t.video);self.assertIsNone(self.t.telemetry)
    def test_late_decode_after_stop_is_discarded(self):
        real=cv2.imdecode
        def decode(*a,**kw):value=real(*a,**kw);self.t.stop();return value
        with patch('cv2.imdecode',side_effect=decode):self.assertFalse(self.t.install_frame(frame(self.t,seq=2),generation=self.t._watch_generation))
        self.assertIsNone(self.t.video)
    def test_same_robot_new_claim_does_not_adopt_old_ack(self):
        old=self.c.claim;self.t.request_claim()
        def raced(rid,channels):
            reply=old(rid,channels);self.t.neutral();self.t.request_claim();return reply
        self.c.claim=raced;self.t.process_jobs();self.assertIsNone(self.t.owned_robot);self.assertTrue(self.t.claim_pending)
        self.assertIsNotNone(self.t._release_job)
    def test_late_open_after_failure(self):
        c=Client();t=PlatformTransport(role='operator',client=c)
        def opened(role):t._failed(ValueError('connection gone'));return dict(session_id='late',role=role)
        c.open=opened;t.connect_once();self.assertIsNone(t.session);self.assertEqual(t.last_error,'connection gone');self.assertFalse(t._connected)
    def test_late_open_after_stop(self):
        c=Client();t=PlatformTransport(role='operator',client=c)
        def opened(role):t.stop();return dict(session_id='late',role=role)
        c.open=opened;t.connect_once();self.assertIsNone(t.session);self.assertFalse(t._connected)

    def test_camera_identity_rejects_boolean_and_float_aliases(self):
        for key,values in [('watch_epoch',[True,1.]),('episode',[False,0.])]:
            for value in values:
                data=frame(self.t,seq=2);data[key]=value
                with self.assertRaises(ValueError):self.t.install_frame(data,generation=self.t._watch_generation)

if __name__=='__main__':unittest.main()
