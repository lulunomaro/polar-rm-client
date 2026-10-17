"""Reject three no-op admission candidates; never changes checked-in source."""
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT))
from tests import test_constructor_admission_matrix as cases

controls = (
    ('serial reader never acquires', cases.RmSerialBackend, 'start', lambda self: None,
     'test_serial_reader_constructor_closes_or_retains_actual_open_handle'),
    ('decoder never owns process', cases.ContinuousMjpegDecoder, 'start', lambda self: False,
     'test_each_decoder_pipe_constructor_releases_or_retains_process'),
    ('Paho never attempts admission', cases.RmMqttBackend, 'start', lambda self: None,
     'test_actual_paho_network_thread_constructor_releases_wakeup_pair'),
)
for label, owner, name, replacement, method in controls:
    result = unittest.TestResult()
    with patch.object(owner, name, replacement):
        cases.NativeConstructorMatrixTests(method).run(result)
    assert not result.wasSuccessful(), label
    print(label, 'rejected:', len(result.failures), 'failures,', len(result.errors), 'errors')
print('PASS: all three deliberately broken admission candidates are rejected')
