"""The HA lid command must fail closed on stale or invalid device data."""
import importlib.util
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / 'custom_components' / 'dreame_sf25'
if 'dreame_sf25' not in sys.modules:
    package = types.ModuleType('dreame_sf25')
    package.__path__ = [str(ROOT)]
    sys.modules['dreame_sf25'] = package
for name in ('const', 'control_guard'):
    if f'dreame_sf25.{name}' not in sys.modules:
        spec = importlib.util.spec_from_file_location(f'dreame_sf25.{name}', ROOT / f'{name}.py')
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)

const = sys.modules['dreame_sf25.const']
can_open_lid = sys.modules['dreame_sf25.control_guard'].can_open_lid


class LidGuardTests(unittest.TestCase):
    def setUp(self):
        self.idle = {
            const.PROP_PROGRAM: -1,
            const.PROP_RUNNING: -1,
            const.PROP_STATUS: 2,
            const.PROP_TEMPERATURE: 39.9,
        }

    def test_confirmed_cool_idle_device_is_allowed(self):
        self.assertTrue(can_open_lid(self.idle))

    def test_hot_or_nonfinite_temperature_is_rejected(self):
        for value in (57, float('nan'), float('inf'), -float('inf')):
            with self.subTest(value=value):
                self.assertFalse(can_open_lid({**self.idle, const.PROP_TEMPERATURE: value}))

    def test_running_paused_or_unconfirmed_state_is_rejected(self):
        changes = (
            {const.PROP_PROGRAM: 0},
            {const.PROP_RUNNING: 0},
            {const.PROP_RUNNING: 1},
            {const.PROP_STATUS: 1},
            {const.PROP_STATUS: 3},
        )
        for change in changes:
            with self.subTest(change=change):
                self.assertFalse(can_open_lid({**self.idle, **change}))

    def test_missing_or_malformed_properties_are_rejected(self):
        for key in self.idle:
            with self.subTest(key=key):
                data = {k: v for k, v in self.idle.items() if k != key}
                self.assertFalse(can_open_lid(data))
        self.assertFalse(can_open_lid({**self.idle, const.PROP_TEMPERATURE: 'bad'}))


if __name__ == '__main__':
    unittest.main()
