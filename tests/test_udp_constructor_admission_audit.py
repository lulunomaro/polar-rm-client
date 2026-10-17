"""A receiver whose thread constructor rejects never owns a running session."""
import unittest
from unittest.mock import patch

from rm_client.transports import UDPVideoReceiver


class UdpConstructorAdmissionAuditTests(unittest.TestCase):
    def test_constructor_rejection_has_no_phantom_session_or_running_status(self):
        receiver = UDPVideoReceiver()
        failure = RuntimeError('receiver constructor rejected')
        with patch('rm_client.transports.threading.Thread', side_effect=failure), \
                patch('rm_client.transports.socket.socket') as socket:
            with self.assertRaises(RuntimeError) as raised:
                receiver.start()
        self.assertIs(raised.exception, failure)
        socket.assert_not_called()
        self.assertFalse(receiver.running)
        self.assertIsNone(receiver._session)
        self.assertIsNone(receiver._thread)
        # Retry must reach admission rather than silently accept a phantom owner.
        retry_failure = RuntimeError('retry reached constructor')
        with patch('rm_client.transports.threading.Thread', side_effect=retry_failure):
            with self.assertRaises(RuntimeError) as raised:
                receiver.start()
        self.assertIs(raised.exception, retry_failure)


if __name__ == '__main__':
    unittest.main()
