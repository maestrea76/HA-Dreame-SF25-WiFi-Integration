"""Protocol error handling: never report unacknowledged writes as successful."""
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import parse_qs

ROOT = Path(__file__).resolve().parents[1] / 'custom_components' / 'dreame_sf25'
if 'dreame_sf25' not in sys.modules:
    package = types.ModuleType('dreame_sf25')
    package.__path__ = [str(ROOT)]
    sys.modules['dreame_sf25'] = package
for name in ('const', 'api'):
    if f'dreame_sf25.{name}' not in sys.modules:
        spec = importlib.util.spec_from_file_location(f'dreame_sf25.{name}', ROOT / f'{name}.py')
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
api = sys.modules['dreame_sf25.api']
const = sys.modules['dreame_sf25.const']

class ApiSafetyTests(unittest.TestCase):
    def setUp(self):
        self.client = api.DreameSF25Client('test@example.invalid', 'unused', 'eu')
        self.client.did = '12345'
        self.client._ensure_token = Mock()

    def test_write_without_acknowledgement_fails(self):
        self.client._set_once = Mock(return_value=(None, None))
        self.client.wake = Mock()
        with self.assertRaises(api.DreameApiError):
            self.client.set_property(6, 17, 1)
        self.client.wake.assert_not_called()

    def test_sleeping_device_is_woken_and_write_retried_once(self):
        self.client._set_once = Mock(side_effect=[({'code': 1}, 1), ({'code': 0}, 0)])
        self.client.wake = Mock()
        self.client.get_properties = Mock(return_value={const.PROP_STATUS: 2})
        with patch.object(api.time, 'sleep') as sleep:
            result = self.client.set_property(6, 17, 1)
        self.assertEqual(result['code'], 0)
        self.client.wake.assert_called_once()
        self.assertEqual(self.client._set_once.call_count, 2)
        sleep.assert_not_called()

    def test_write_waits_until_device_leaves_suspend(self):
        self.client._set_once = Mock(side_effect=[({'code': 1}, 1), ({'code': 0}, 0)])
        self.client.wake = Mock()
        self.client.get_properties = Mock(side_effect=[
            {const.PROP_STATUS: 3},
            {const.PROP_STATUS: 3},
            {const.PROP_STATUS: 2},
        ])
        clock = {'t': 0.0}

        def monotonic():
            return clock['t']

        def sleep(seconds):
            clock['t'] += seconds

        with patch.object(api.time, 'monotonic', monotonic), patch.object(api.time, 'sleep', sleep):
            result = self.client.set_property(6, 17, 1)
        self.assertEqual(result['code'], 0)
        self.assertEqual(self.client.get_properties.call_count, 3)
        self.assertGreater(clock['t'], 0)

    def test_child_lock_rejected_while_awake_names_the_reason(self):
        self.client._set_once = Mock(return_value=({'code': 1}, 1))
        self.client.wake = Mock()
        self.client.get_properties = Mock(return_value={const.PROP_STATUS: 2})
        with patch.object(api.time, 'sleep'), self.assertRaises(api.DreameApiError) as raised:
            self.client.set_property(6, 10, 1)
        self.assertIn('bloqueo infantil', str(raised.exception))
        self.assertIn('ciclo en marcha', str(raised.exception))

    def test_write_rejected_when_device_stays_suspended(self):
        self.client._set_once = Mock(return_value=({'code': 1}, 1))
        self.client.wake = Mock()
        self.client.get_properties = Mock(return_value={const.PROP_STATUS: 3})
        clock = {'t': 0.0}

        def monotonic():
            return clock['t']

        def sleep(seconds):
            clock['t'] += seconds

        with patch.object(api.time, 'monotonic', monotonic), patch.object(api.time, 'sleep', sleep):
            with self.assertRaises(api.DreameApiError) as raised:
                self.client.set_property(6, 10, 1)
        self.assertIn('suspension', str(raised.exception))
        self.assertNotIn('ciclo en marcha', str(raised.exception))
        self.assertGreaterEqual(clock['t'], api.WAKE_WAIT_SECONDS)

    def test_wake_timeout_does_not_hide_successful_retried_write(self):
        self.client._set_once = Mock(side_effect=[({'code': 1}, 1), ({'code': 0}, 0)])
        self.client.wake = Mock(side_effect=api.DreameApiError('cloud timeout'))
        self.client.get_properties = Mock(return_value={const.PROP_STATUS: 2})
        with patch.object(api.time, 'sleep'):
            result = self.client.set_property(2, 3, 0)
        self.assertEqual(result['code'], 0)

    def test_action_accepts_empty_success_without_duplicate_write(self):
        self.client._api = Mock(return_value={'success': True})
        self.assertIsNone(self.client.run_action(2, 1))
        self.client._api.assert_called_once()

    def test_action_rejects_failure(self):
        self.client._api = Mock(return_value={'success': False, 'code': 500})
        with self.assertRaises(api.DreameApiError):
            self.client.run_action(2, 1)

    def test_refresh_token_is_form_encoded(self):
        self.client._refresh_token = 'a+b/='
        self.client._do_token = Mock()
        self.client._refresh()
        body = self.client._do_token.call_args.args[0]
        self.assertEqual(parse_qs(body)['refresh_token'], ['a+b/='])

    def test_resolve_device_rejects_same_id_with_wrong_model(self):
        self.client.list_devices = Mock(return_value=[{'did': '12345', 'model': 'dreame.vacuum.other'}])
        with self.assertRaises(api.DreameApiError):
            self.client.resolve_device('12345')

    def test_cloud_shard_is_not_a_free_form_path(self):
        self.client.bind_host = 'oops/path.mt.eu.iot.dreame.tech:19973'
        with self.assertRaises(api.DreameApiError):
            self.client._rpc_path()

if __name__ == '__main__':
    unittest.main()
