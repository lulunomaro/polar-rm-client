"""Run from the reviewed checkout with PYTHONPATH=.:tests."""
from itertools import combinations
from types import SimpleNamespace
from test_focus_loss_failures import FocusLossFailureTests
from rm_client.emergency_actions import request_focus_loss

operations = ('lock', 'release', 'reset', 'dt7', 'cc', 'zero', 'clear')
factory = FocusLossFailureTests()
for bits in range(128):
    failures = {name for i, name in enumerate(operations) if bits & (1 << i)}
    host = factory.host(failures)
    host.focusOutEvent(object())
    expected = ['lock', 'release']
    if 'release' in failures:
        expected += ['reset', 'dt7', 'cc']
    expected += ['zero', 'clear', 'qt-focus']
    assert host.trace == expected, (failures, host.trace)
    observed_failures = set(expected) & failures
    assert bool(host.warnings) == bool(observed_failures), failures
    if host.warnings:
        warning = host.warnings[0][0]
        for name in observed_failures:
            assert name + ' failed' in warning, (failures, warning)
        if 'lock' in failures:
            assert 'LOCKED' not in warning, warning
    assert host.student_ui.dragging == ''

# Independently inject failures in attribute resolution rather than method bodies.
for broken in ('reset_inputs', 'release_direct_dt7', 'release_custom_controller', 'clear_pending'):
    events = []
    class Inputs:
        def __getattribute__(self, name):
            if name == broken:
                events.append('lookup:' + name)
                raise RuntimeError('lookup failed: ' + name)
            return lambda: events.append(name)
    def fail_release():
        events.append('release')
        raise RuntimeError('release failed')
    backend = SimpleNamespace(lock_control=lambda reason: events.append('lock'))
    result = request_focus_loss(backend, Inputs(), fail_release,
                                lambda: (events.append('zero') or (True, '')))
    assert events == ['lock', 'release'] + [
        'lookup:' + name if name == broken else name
        for name in ('reset_inputs', 'release_direct_dt7', 'release_custom_controller')
    ] + ['zero', 'lookup:clear_pending' if broken == 'clear_pending' else 'clear_pending'], events
    assert any('lookup failed: ' + broken in failure.message for failure in result.failures)
print('PASS: 128 independent cleanup-failure combinations; four fallback attribute-resolution failures; Qt delegation and accurate lock-status reporting')
