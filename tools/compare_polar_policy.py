"""Differentially replay the POLAR policy refactor against a pinned Git source.

No baseline worktree, device, network, workers or app startup is needed. Both
managers use the same recording adapter and codec; separate protocol tests
remain responsible for the encoder itself. This tool never changes a fixture.

Example:
    python tools/compare_polar_policy.py --cases 3000 --json /tmp/polar-diff.json
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import logging
from pathlib import Path
import random
import subprocess
import sys
import types

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
BASELINE = 'd64f1528e2e1864ef369537c026b6d3f7127f590'
LOCAL_BASELINE_ALIAS = 'da91fc54b97caffd8f76b1aba86f88105d3c0379'
BASELINE_MANAGER_SHA256 = '5c3734c24ab3be3f59791ef1fbf0e85fe7e6a1790736884d9a4f75fc2c8756d1'
MANAGER_PATH = 'rm_client/backend/manager.py'


def baseline_source(ref):
    """Use local Git objects only, accepting an alias only for identical bytes."""
    references = (ref, LOCAL_BASELINE_ALIAS) if ref == BASELINE else (ref,)
    for candidate in references:
        try:
            result = subprocess.run(
                ['git', 'show', f'{candidate}:{MANAGER_PATH}'], cwd=ROOT,
                capture_output=True, check=False)
        except OSError as exc:
            raise RuntimeError(f'Cannot read local Git history: {exc}') from exc
        if result.returncode:
            continue
        digest = hashlib.sha256(result.stdout).hexdigest()
        if digest != BASELINE_MANAGER_SHA256:
            raise RuntimeError(
                f'Refusing baseline {candidate}: manager SHA-256 {digest} does not '
                f'match the frozen source {BASELINE_MANAGER_SHA256}')
        return result.stdout, candidate
    raise RuntimeError(
        f'Pinned public baseline {BASELINE} is unavailable in local Git history. '
        'Make that revision available locally, or pass --baseline <local-ref> '
        f'containing manager SHA-256 {BASELINE_MANAGER_SHA256}. '
        'This tool never fetches history or rewrites the frozen fixture.')


def baseline_manager(ref):
    source, resolved_ref = baseline_source(ref)
    module = types.ModuleType('rm_client.backend._policy_reference')
    module.__file__ = f'{resolved_ref}:{MANAGER_PATH}'
    module.__package__ = 'rm_client.backend'
    exec(compile(source, module.__file__, 'exec'), module.__dict__)
    return module.BackendManager, hashlib.sha256(source).hexdigest(), resolved_ref


def characterization_harness():
    path = ROOT / 'tests/test_backend_policy_characterization.py'
    spec = importlib.util.spec_from_file_location('_polar_characterization_harness', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def random_cases(count, seed):
    rng = random.Random(seed)
    for index in range(count):
        settings = {
            name: rng.choice((True, False)) for name in (
                'Allow Drive', 'Client Chassis Drive', 'Client Gimbal Control',
                'Vision NUC Occupied', 'Allow Spin Key', 'Radio KM Egress', 'Mock Writes',
            )
        }
        settings.update({
            'Speed Scale %': rng.choice((0, 25, 50, 100, 125)),
            'Yaw Scale %': rng.choice((0, 25, 50, 100, 125)),
            'Gimbal Slew': rng.choice((0, 1, 8, 45, 50)),
            'Port': rng.choice(('Auto', '', 'Stub', ' COM7 ')),
            'Baud': rng.choice((115200, 57600, '921600', 0, 3000001, 'bad')),
            'Link Mode': rng.choice(('serial', 'tcp', 'TCP', 'invalid')),
            'Bridge Host': rng.choice(('', 'robot.example.invalid')),
            'Bridge Port': rng.choice((50570, '50571', None, 'bad')),
            'Send Hz': rng.choice((0, 5, 50, 100, 1000, 'bad')),
            'Drive Channel': rng.choice(('robot_cmd', 'virtual_rc', 'Virtual RC', 'both', 'invalid')),
        })
        yield f'random_{index:05d}', {
            'settings': settings,
            **{name: rng.choice((False, True)) for name in (
                'estop', 'enabled', 'active', 'calibrated', 'locked', 'spin',
                'spin_ever_on', 'lockout', 'no_command', 'no_control', 'running', 'reconfigure',
            )},
            'selected': rng.choice((0, 1, 2, 3, 4, 6, 7, 99, 101, 103)),
            'reported': rng.choice((0, 1, 3, 7, 99, 101, 103)),
            'debug': {'km_rx': rng.choice((None, 'bad', 0, -1, 1, 4, '3.9')),
                      'km_key': rng.choice((None, 'bad', 0, 1, 0xFFFF)),
                      'km_pol': rng.choice((None, 'bad', 0, 1, 2, 3, 4, 7))},
            'previous_rx': rng.choice((None, 0, 1, 4)),
            'debug_age': rng.choice((0, 0.75, 0.75001, -0.001)),
            'spin_reported': rng.choice((None, False, True)),
        }


def failure_cases():
    """Boundary errors must preserve prior revocation and side effects too."""
    for name, key in (("counter", "km_rx"), ("key", "km_key"), ("policy", "km_pol")):
        value = float('inf') if key == "km_pol" else "inf"
        for estop in (False, True):
            yield f'failure_radio_{name}_estop_{estop}', {
                'settings': {'Radio KM Egress': True}, 'radio_initial': True,
                'debug': {key: value}, 'estop': estop,
            }
    for name in ("Gimbal Slew", "Speed Scale %", "Yaw Scale %"):
        for value in (None, "bad", [], {}):
            for closing in (False, True):
                yield f'failure_setting_{name}_{value!r}_closing_{closing}', {
                    'settings': {name: value, 'Port': 'COM7'}, 'reconfigure': not closing,
                }
    for calibrated, requested, estop in ((False, False, False), (True, True, False),
                                       (True, True, True), (False, True, False)):
        yield f'failure_identity_{calibrated}_{requested}_{estop}', {
            'selected': 'bad', 'prior_plan': (1.0, 2.0), 'calibrated': calibrated,
            'estop': estop, 'settings': {'Client Gimbal Control': requested,
                                        'Vision NUC Occupied': False},
        }


def observe(harness, manager, options):
    harness.BackendManager = manager
    try:
        return {'observation': harness.characterize(options, capture_failures=True)}
    except Exception as error:
        # A failure outside the tick cannot supply a complete observation.
        return {'unobserved_exception': type(error).__name__}


def first_difference(before, after, path=''):
    if type(before) is not type(after):
        return path, before, after
    if isinstance(before, dict):
        for key in sorted(set(before) | set(after)):
            if key not in before or key not in after:
                return f'{path}.{key}', before.get(key), after.get(key)
            difference = first_difference(before[key], after[key], f'{path}.{key}')
            if difference:
                return difference
    elif isinstance(before, list):
        if len(before) != len(after):
            return path + '.length', len(before), len(after)
        for index, (old, new) in enumerate(zip(before, after)):
            difference = first_difference(old, new, f'{path}[{index}]')
            if difference:
                return difference
    elif before != after:
        return path, before, after
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', default=BASELINE)
    parser.add_argument('--cases', type=int, default=3000)
    parser.add_argument('--seed', type=int, default=20261016)
    parser.add_argument('--json', type=Path)
    args = parser.parse_args()
    if args.cases < 0:
        parser.error('--cases cannot be negative')
    try:
        reference, baseline_hash, resolved_ref = baseline_manager(args.baseline)
    except RuntimeError as exc:
        print(f'INCOMPLETE: {exc}', file=sys.stderr)
        return 2
    harness = characterization_harness()
    candidate = harness.BackendManager
    logging.disable(logging.CRITICAL)
    differences = []
    executed = exceptions = unobserved = 0
    digest = hashlib.sha256()
    scenarios = [*harness.cases().items(), *random_cases(args.cases, args.seed), *failure_cases()]
    for name, options in scenarios:
        before = observe(harness, reference, options)
        after = observe(harness, candidate, options)
        executed += 1
        exceptions += int('failure' in before.get('observation', {})
                          or 'failure' in after.get('observation', {}))
        unobserved += int('unobserved_exception' in before or 'unobserved_exception' in after)
        digest.update(json.dumps([name, options, before, after], sort_keys=True).encode())
        difference = first_difference(before, after)
        if difference:
            differences.append({'case': name, 'options': options,
                                'first_difference': difference})
    report = {
        'baseline': args.baseline, 'baseline_manager_sha256': baseline_hash,
        'baseline_source_ref': resolved_ref,
        'candidate_manager_sha256': hashlib.sha256((ROOT / MANAGER_PATH).read_bytes()).hexdigest(),
        'seed': args.seed, 'cases': executed, 'aborted_cases': exceptions,
        'unobserved_failures': unobserved,
        'abort_comparison': 'exception type plus partial control/state/config/events; error text excluded',
        'mismatches': differences, 'replay_sha256': digest.hexdigest(),
        'scope': 'recording-fake policy and codec replay; no hardware qualification',
    }
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'{executed} cases, {len(differences)} mismatches, {exceptions} aborted cases, {unobserved} unobserved failures')
    for difference in differences[:10]:
        print(json.dumps(difference, ensure_ascii=False))
    return 1 if differences else 2 if unobserved else 0


if __name__ == '__main__':
    raise SystemExit(main())
