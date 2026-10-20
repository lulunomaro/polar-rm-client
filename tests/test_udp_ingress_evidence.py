"""Real loopback transport checks, not physical receiver certification."""
from pathlib import Path
import argparse
import socket
import struct
import sys
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rm_client.transports import UDPVideoReceiver
from tools.video_ingress_probe import summarize, positive_duration


def packet(frame: int, index: int, total: int, data: bytes) -> bytes:
    return struct.pack(">HHI", frame, index, total) + data


class UdpIngressEvidenceTests(unittest.TestCase):
    def wait_for(self, predicate):
        deadline = time.monotonic() + 2.0
        while not predicate() and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertTrue(predicate())

    def receiver(self):
        received = []
        receiver = UDPVideoReceiver(port=0, on_frame=received.append)
        self.addCleanup(receiver.stop)
        receiver.start()
        self.wait_for(lambda: receiver.ingress_stats()["listening"])
        return receiver, received, receiver.ingress_stats()["bound_address"]

    def sender(self):
        sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sender.bind(("127.0.0.1", 0))
        self.addCleanup(sender.close)
        return sender

    def test_different_senders_cannot_complete_one_frame(self):
        receiver, frames, address = self.receiver()
        first, second = self.sender(), self.sender()
        first.sendto(packet(7, 0, 4, b"AA"), address)
        self.wait_for(lambda: receiver.ingress_stats()["accepted_packets"] == 1)
        second.sendto(packet(7, 1, 4, b"BB"), address)
        self.wait_for(lambda: receiver.packets_received == 2)
        self.assertEqual(frames, [])
        self.assertEqual(receiver.ingress_stats()["foreign_packets"], 1)
        self.assertEqual(receiver._assembler.buffered_bytes, 2)
        first.sendto(packet(7, 1, 4, b"CC"), address)
        self.wait_for(lambda: frames == [b"AACC"])
        self.assertEqual(receiver.ingress_stats()["sender_address"], first.getsockname())

    def test_malformed_first_packet_cannot_select_sender(self):
        receiver, frames, address = self.receiver()
        first, second = self.sender(), self.sender()
        # Impossible one-fragment completed frame: the index must begin at zero.
        first.sendto(packet(1, 3, 1, b"x"), address)
        self.wait_for(lambda: receiver.packets_received == 1)
        self.assertIsNone(receiver.ingress_stats()["sender_address"])
        self.assertEqual(receiver.ingress_stats()["accepted_packets"], 0)
        second.sendto(packet(1, 0, 1, b"y"), address)
        self.wait_for(lambda: frames == [b"y"])
        self.assertEqual(receiver.ingress_stats()["sender_address"], second.getsockname())

    def test_explicit_restart_admits_new_endpoint_and_resets_evidence(self):
        receiver, frames, address = self.receiver()
        first, second = self.sender(), self.sender()
        first.sendto(packet(8, 0, 4, b"AA"), address)
        self.wait_for(lambda: receiver.ingress_stats()["accepted_packets"] == 1)
        receiver.stop()
        self.assertTrue(receiver.wait_closed(timeout=1))
        self.assertFalse(receiver.ingress_stats()["listening"])
        self.assertIsNone(receiver.ingress_stats()["bound_address"])
        receiver.start()
        self.wait_for(lambda: receiver.ingress_stats()["listening"])
        self.assertEqual(receiver.packets_received, 0)
        self.assertEqual(receiver.ingress_stats()["accepted_packets"], 0)
        self.assertIsNone(receiver.ingress_stats()["sender_address"])
        address = receiver.ingress_stats()["bound_address"]
        second.sendto(packet(8, 1, 4, b"BB"), address)
        self.wait_for(lambda: receiver.packets_received == 1)
        self.assertEqual(frames, [])
        second.sendto(packet(8, 0, 4, b"CC"), address)
        self.wait_for(lambda: frames == [b"CCBB"])

    def test_occupied_port_never_claims_listening(self):
        occupied = self.sender()
        receiver = UDPVideoReceiver(host="127.0.0.1", port=occupied.getsockname()[1])
        self.addCleanup(receiver.stop)
        receiver.start()
        self.wait_for(lambda: bool(receiver.last_error))
        self.assertFalse(receiver.ingress_stats()["listening"])
        self.assertIsNone(receiver.ingress_stats()["bound_address"])
        self.assertFalse(receiver.running)

    def test_probe_stage_does_not_certify_hardware(self):
        observed = dict(listening=True, packets=True, assembled=True, decoded=False)
        report = summarize({"decoded_connected": False}, observed=observed, cleanup_complete=True)
        self.assertEqual(report["stage"], "assembled_but_not_decoded")
        self.assertFalse(report["hardware_verified"])
        observed["decoded"] = True
        report = summarize({"decoded_connected": False}, observed=observed, cleanup_complete=True)
        self.assertEqual(report["stage"], "decoded_video_became_stale")
        report = summarize({"decoded_connected": True}, observed=observed, cleanup_complete=True)
        self.assertEqual(report["stage"], "fresh_decoded_video")
        self.assertFalse(report["hardware_verified"])

    def test_probe_duration_is_bounded_and_finite(self):
        for value in ("nan", "inf", "-1", "0", "301"):
            with self.assertRaises(argparse.ArgumentTypeError):
                positive_duration(value)
        self.assertEqual(positive_duration("0.1"), 0.1)


if __name__ == "__main__":
    unittest.main()
