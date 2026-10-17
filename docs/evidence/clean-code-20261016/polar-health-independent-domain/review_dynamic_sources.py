"""Independent dynamic source replacement through both actual manager callers."""
from unittest.mock import patch
from types import SimpleNamespace
import test_polar_health_characterization as harness
from rm_client.backend import manager

original_get = harness.Source.__getattr__
original_fixture = harness.fixture
counts = {}

def fixture(trace):
    counts.clear()
    return original_fixture(trace)

def changing_get(self, name):
    result = original_get(self, name)
    if self._name == 'manager' and name in ('polar', 'polar_bridge'):
        counts[name] = counts.get(name, 0) + 1
        ordinal = counts[name]
        # Produce a newly replaced component on each manager observation.
        # This rejects accidental eager capture of the transport or stats bridge.
        values = dict(result._values)
        if name == 'polar':
            old_stats = result._values['codec']._values['stats']
            counters = {key: ordinal * 100 + index for index, key in enumerate(old_stats._values)}
            values['codec'] = harness.Source(self._trace, 'changing_codec',
                stats=harness.Source(self._trace, 'changing_decode', **counters))
        else:
            values['dropped'] = ordinal
            values['active'] = bool(ordinal % 2)
        result = harness.Source(self._trace, f'changing_{name}_{ordinal}', **values)
    return result

checks = 0
with patch.object(harness, 'fixture', fixture), patch.object(harness.Source, '__getattr__', changing_get), \
        patch.object(manager.time, 'perf_counter', return_value=11.):
    for name in ('stats', '_publish_polar_authority'):
        for estopped in (False, True):
            old = harness.frozen_methods()[name]
            new = getattr(manager.BackendManager, name)
            expected = harness.exercise(old, name, estopped=estopped)
            actual = harness.exercise(new, name, estopped=estopped)
            assert expected == actual, (name, estopped, expected, actual)
            checks += 1
            for fail_at in range(1, len(expected[0]) + 1):
                assert harness.exercise(old, name, estopped=estopped, fail_at=fail_at) == \
                    harness.exercise(new, name, estopped=estopped, fail_at=fail_at), (name, estopped, fail_at)
                checks += 1
print(f'PASS: {checks} dynamic replacement and full-caller failure-prefix comparisons')
