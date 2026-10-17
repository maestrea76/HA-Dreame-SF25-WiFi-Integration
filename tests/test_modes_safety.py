"""Safety regressions for SF25 virtual programs; no device or HA installation needed."""
import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock

ROOT = Path(__file__).resolve().parents[1] / 'custom_components' / 'dreame_sf25'
package = types.ModuleType('dreame_sf25')
package.__path__ = [str(ROOT)]
sys.modules['dreame_sf25'] = package
for name in ('homeassistant', 'homeassistant.helpers'):
    module = types.ModuleType(name)
    module.__path__ = []
    sys.modules[name] = module
core = types.ModuleType('homeassistant.core')
core.HomeAssistant = object
core.callback = lambda fn: fn
sys.modules[core.__name__] = core
exceptions = types.ModuleType('homeassistant.exceptions')
exceptions.HomeAssistantError = type('HomeAssistantError', (Exception,), {})
sys.modules[exceptions.__name__] = exceptions
event = types.ModuleType('homeassistant.helpers.event')
event.async_call_later = lambda *args, **kwargs: lambda: None
event.async_track_time_change = lambda *args, **kwargs: lambda: None
sys.modules[event.__name__] = event
storage = types.ModuleType('homeassistant.helpers.storage')
class Store:
    def __init__(self, *args): pass
    async def async_save(self, data): pass
    async def async_load(self): return {}
storage.Store = Store
sys.modules[storage.__name__] = storage
for name in ('const', 'modes'):
    spec = importlib.util.spec_from_file_location(f'dreame_sf25.{name}', ROOT / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

const = sys.modules['dreame_sf25.const']
Modes = sys.modules['dreame_sf25.modes'].DreameSF25Modes

class ModesSafetyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.calls = []
        self.hass = types.SimpleNamespace(
            async_add_executor_job=self.executor,
            async_create_task=lambda coro: asyncio.create_task(coro),
        )
        self.coordinator = types.SimpleNamespace(
            data={const.PROP_PROGRAM: const.PROGRAM_OPTIONS['cycle']},
            client=types.SimpleNamespace(set_property=self.set_property),
            entry=types.SimpleNamespace(options={}),
            async_request_refresh=AsyncMock(),
            async_update_listeners=Mock(),
        )
        self.modes = Modes(self.hass, self.coordinator, 'test-entry')

    async def executor(self, fn, *args):
        return fn(*args)

    def set_property(self, *args):
        self.calls.append(args)

    async def test_virtual_mode_cannot_interrupt_running_cycle(self):
        self.coordinator.entry.options[const.CONF_VIRTUAL_MODES] = True
        with self.assertRaises(exceptions.HomeAssistantError):
            await self.modes.async_start_virtual(const.PROGRAM_STIR)
        self.assertEqual(self.calls, [])
        self.assertIsNone(self.modes.virtual_mode)

    async def test_failed_start_does_not_arm_stop_timer(self):
        self.coordinator.entry.options[const.CONF_VIRTUAL_MODES] = True
        self.coordinator.data[const.PROP_PROGRAM] = const.PROGRAM_OPTIONS['idle']
        self.coordinator.client.set_property = Mock(side_effect=RuntimeError('rejected'))
        with self.assertRaises(RuntimeError):
            await self.modes.async_start_virtual(const.PROGRAM_STIR)
        self.assertIsNone(self.modes.virtual_mode)
        self.assertFalse(self.modes._owns_program)
        self.assertIsNone(self.modes._unsub_timer)

    async def test_expired_virtual_mode_does_not_stop_another_program(self):
        self.modes.virtual_mode = const.PROGRAM_STIR
        self.modes._owns_program = True
        await self.modes._async_finish_virtual()
        self.assertEqual(self.calls, [])
        self.assertIsNone(self.modes.virtual_mode)

    async def test_virtual_mode_requires_explicit_opt_in(self):
        self.coordinator.data[const.PROP_PROGRAM] = const.PROGRAM_OPTIONS['idle']
        with self.assertRaises(exceptions.HomeAssistantError):
            await self.modes.async_start_virtual(const.PROGRAM_STIR)
        self.assertEqual(self.calls, [])

    async def test_automatic_mode_is_off_by_default(self):
        self.coordinator.data[const.PROP_PROGRAM] = const.PROGRAM_OPTIONS['idle']
        self.modes.lid_count = const.LID_COUNT_THRESHOLD - 1
        await self.modes._async_lid_closed(const.PROGRAM_OPTIONS['idle'])
        self.assertEqual(self.modes.lid_count, const.LID_COUNT_THRESHOLD)
        self.assertEqual(self.calls, [])

if __name__ == '__main__':
    unittest.main()
