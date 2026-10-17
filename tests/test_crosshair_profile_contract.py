"""Frozen pre-extraction wire/profile behavior; no Qt or device construction."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import random
from types import SimpleNamespace
import unittest

from rm_client import cs2_crosshair as current

_SPEC = importlib.util.spec_from_file_location(
    'crosshair_profile_baseline', Path(__file__).parent / 'fixtures/crosshair_profile_baseline.py')
baseline = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(baseline)

CODES = (
    'CSGO-Cn37R-YE7vo-pLCAL-aURmZ-z6zkG',
    'CSGO-MnUCC-89iG7-2cVar-wy7Yn-amCpF',
    'CSGO-G8oAC-RyvWc-Hi3CZ-voSwn-QJbfE',
    'CSvbPubOq37zTGqtsPTP5QTrp5CB4xFXiKRLfzJsm49ZRe',
    'CSHt3ObzKS3ikGJLRBVrKjPUrhOCikyke53na8cJFtNZKa',
    'CSAB6Oby2JUDOrBRy4jRcsR6JOQdhbszKF9GuKQGhKVwAc',
)


def outcome(module, name, *args, **kwargs):
    try:
        return ('value', getattr(module, name)(*args, **kwargs))
    except Exception as exc:
        return ('error', type(exc), str(exc))


class CrosshairProfileContract(unittest.TestCase):
    def same(self, name, *args, **kwargs):
        self.assertEqual(outcome(current, name, *args, **kwargs),
                         outcome(baseline, name, *args, **kwargs))

    def test_public_vectors_and_export_formats(self):
        for code in CODES:
            with self.subTest(code=code):
                self.same('decode_share_code', code)
                v = baseline.decode_share_code(code)
                for fmt in baseline.FORMATS:
                    self.same('encode_share_code', v, fmt)
                    self.same('export_cfg', v, fmt)
                self.same('export_json', v)
                self.same('profile_notice', v)
                self.assertEqual(current.encode_share_code(v), code)

    def test_all_numeric_field_boundaries(self):
        for field, (lo, hi) in baseline.FIELDS.items():
            for value in (lo, hi, lo - 1, hi + 1, (lo + hi) / 2, True, None,
                          '1', float('inf'), float('nan'), 10 ** 400):
                with self.subTest(field=field, value=str(value)):
                    self.same('validate_profile', {field: value})

    def test_boolean_and_text_validation(self):
        for field in baseline.BOOLS:
            for value in (True, False, 0, 1, 0.0, 1.0, 2, None, 'true'):
                self.same('validate_profile', {field: value})
        for values in (None, [], {'unknown': 1}, {'Source Format': 'legacy-v2'},
                       {'Geometry Units': 'mm'}, {'Import Notes': 'x' * 10000},
                       {'Imported Code': 2}, {'Import Notes': None}):
            self.same('validate_profile', values)

    def test_parse_json_alias_conflicts_and_cfg_rejections(self):
        texts = ('', '[]', '{}', '{', '{"Length": 1, "Length": 2}',
                 '{"length": 1, "Length": 1}', '{"outlineEnabled": 2}',
                 '{"format":"legacy-v1","length":3}',
                 'cl_crosshairsize 3; cl_crosshair_gap 2', 'exec attack.cfg',
                 'cl_crosshair_length 2 // ; attack',
                 'cl_crosshair_length 2; cl_crosshair_length 3',
                 'cl_crosshair_length 2; cl_crosshairsize 2',
                 'cl_crosshair_length 1e500', '\ufeff {"length":4} ',
                 'x' * 65537, '中' * 22000, None, b'abc')
        for text in texts:
            self.same('parse_external', text)
        for code in CODES:
            v = baseline.decode_share_code(code)
            self.same('parse_external', baseline.export_json(v))
            cfg = outcome(baseline, 'export_cfg', v)
            if cfg[0] == 'value':
                self.same('parse_external', cfg[1])

    def test_random_wire_mutations_keep_sanitation_errors_and_unused_bits(self):
        rng = random.Random(20261016)
        for code in CODES:
            _, original, container = baseline._share_bytes(code)
            for _ in range(40):
                data = bytearray(original)
                data[rng.randrange(1, len(data))] = rng.randrange(256)
                data[0] = sum(data[1:]) % 256
                number, chars = int.from_bytes(data, 'big'), []
                for _ in range(44 if container == 'cs2' else 25):
                    number, digit = divmod(number, 57)
                    chars.append(baseline.ALPHABET[digit])
                raw = ''.join(chars)
                mutated = 'CS' + raw if container == 'cs2' else 'CSGO-' + '-'.join(raw[i:i+5] for i in range(0, 25, 5))
                self.same('decode_share_code', mutated)
                decoded = outcome(baseline, 'decode_share_code', mutated)
                if decoded[0] == 'value':
                    self.same('encode_share_code', decoded[1])

    def test_profile_results_are_detached(self):
        first = current.validate_profile({})
        first['Length'] = 100
        self.assertEqual(current.validate_profile({})['Length'], baseline.DEFAULTS['Length'])
        self.assertEqual(current.DEFAULTS, baseline.DEFAULTS)

    def test_module_import_preflights_every_field_before_writing(self):
        for missing in (*baseline.DEFAULTS, 'Style'):
            settings = {key: SimpleNamespace(value='old') for key in (*baseline.DEFAULTS, 'Style')}
            del settings[missing]
            module = SimpleNamespace(get_setting=settings.get)
            with self.assertRaises(ValueError):
                current.apply_profile(module, {})
            self.assertTrue(all(s.value == 'old' for s in settings.values()))

    def test_module_import_write_order_and_failure_prefix(self):
        for failure in (None, 'Length', 'Alpha', 'Style'):
            traces = []
            for implementation in (baseline, current):
                events = []
                class Setting:
                    def __init__(self, key):
                        self.key = key
                    @property
                    def value(self):
                        return 5
                    @value.setter
                    def value(self, value):
                        events.append(('write', self.key, value))
                        if self.key == failure:
                            raise RuntimeError('setter failed')
                def get(key):
                    events.append(('get', key))
                    return Setting(key)
                result = outcome(implementation, 'apply_profile', SimpleNamespace(get_setting=get), {})
                traces.append((events, result))
            self.assertEqual(*traces)

    def test_catalog_profile_schema_matches_wire_ranges_and_defaults(self):
        from rm_client.module_catalog import CatalogChoices, default_modules
        choices = CatalogChoices(("LabDebug",), ("RMUC7v7",), ("Infantry1",), "test", ("test",),
                                 ("Stub",), ("Stub",), ("Auto",), ("Auto",), ("Auto",))
        module = next(m for m in default_modules(choices) if m.name == 'Crosshair')
        for key, default in baseline.DEFAULTS.items():
            setting = module.get_setting(key)
            self.assertEqual(setting.value, default)
            if key in baseline.FIELDS:
                self.assertEqual((setting.min_value, setting.max_value), baseline.FIELDS[key])
        # Catalog deliberately retains integral defaults even where wire defaults
        # are floats. Extraction must not normalize user-visible setting values.
        self.assertIs(type(module.get_setting('Length').value), int)
        self.assertIs(type(module.get_setting('Thickness').value), int)

    def test_none_module_and_invalid_values_precedence(self):
        self.same('apply_profile', None, {'bad': 1})
        self.same('apply_profile', None, {})
        self.same('profile_from_module', None)
        self.same('profile_from_module', SimpleNamespace(get_setting=lambda key: None))


if __name__ == '__main__':
    unittest.main()
