import io
import json
import os
import tempfile
import time
import unittest
from unittest import mock

import kittymock  # noqa: F401  (plugins/ в sys.path)
from modules import update as U


class UpdateTestCase(unittest.TestCase):
    """Общая изоляция: кэш — во временной папке, env — свой."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._env = {k: os.environ.get(k)
                     for k in ('XDG_CACHE_HOME', U.UPDATE_ENV)}
        os.environ['XDG_CACHE_HOME'] = self._tmp.name
        os.environ.pop(U.UPDATE_ENV, None)

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmp.cleanup()

    def write_cache(self, **data):
        path = U._cache_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f)


class ParseVersionTests(unittest.TestCase):
    def test_plain_and_v_prefixed(self):
        self.assertEqual(U.parse_version('0.17.0'), (0, 17, 0))
        self.assertEqual(U.parse_version('v1.2.3'), (1, 2, 3))

    def test_garbage_is_none(self):
        self.assertIsNone(U.parse_version(''))
        self.assertIsNone(U.parse_version('0.17.x'))


class UpdateHintTests(UpdateTestCase):
    def test_hint_shown_once_a_day(self):
        self.write_cache(latest='0.17.0')
        with mock.patch.object(U, 'installed_version', return_value='0.16.0'):
            self.assertEqual(U.update_hint(),
                             'familiar 0.17.0 is out — brew upgrade familiar')
            # notified записан — повторное открытие кита молчит
            self.assertIsNone(U.update_hint())

    def test_no_hint_when_up_to_date(self):
        self.write_cache(latest='0.16.0')
        with mock.patch.object(U, 'installed_version', return_value='0.16.0'):
            self.assertIsNone(U.update_hint())

    def test_no_hint_without_cache_or_version(self):
        with mock.patch.object(U, 'installed_version', return_value='0.16.0'):
            self.assertIsNone(U.update_hint())
        self.write_cache(latest='0.17.0')
        with mock.patch.object(U, 'installed_version', return_value=None):
            self.assertIsNone(U.update_hint())

    def test_opt_out_env(self):
        self.write_cache(latest='9.9.9')
        os.environ[U.UPDATE_ENV] = '0'
        with mock.patch.object(U, 'installed_version', return_value='0.16.0'):
            self.assertIsNone(U.update_hint())


class FetchLatestTests(unittest.TestCase):
    def fetch(self, payload):
        body = json.dumps(payload).encode()
        resp = mock.MagicMock()
        resp.__enter__.return_value = io.BytesIO(body)
        with mock.patch.object(U.urllib.request, 'urlopen', return_value=resp):
            return U._fetch_latest()

    def test_max_semver_wins_regardless_of_order(self):
        tags = [{'name': 'v0.9.0'}, {'name': 'demo'}, {'name': 'v0.17.0'},
                {'name': 'v0.16.0'}]
        self.assertEqual(self.fetch(tags), '0.17.0')

    def test_no_version_tags_is_none(self):
        self.assertIsNone(self.fetch([{'name': 'demo'}]))
        self.assertIsNone(self.fetch({'message': 'rate limited'}))


class StartCheckTests(UpdateTestCase):
    def test_fresh_cache_skips_fetch(self):
        self.write_cache(checked=time.time())
        with mock.patch.object(U.threading, 'Thread') as thread:
            U.start_check()
        thread.assert_not_called()

    def test_opt_out_skips_fetch(self):
        os.environ[U.UPDATE_ENV] = '0'
        with mock.patch.object(U.threading, 'Thread') as thread:
            U.start_check()
        thread.assert_not_called()

    def test_stale_cache_refreshes(self):
        self.write_cache(checked=time.time() - 2 * U.INTERVAL)
        with mock.patch.object(U.threading, 'Thread') as thread, \
                mock.patch.object(U, '_fetch_latest', return_value='0.17.0'):
            U.start_check()
            thread.assert_called_once()
            thread.call_args.kwargs['target']()   # тело потока — синхронно
        data = U._load()
        self.assertEqual(data['latest'], '0.17.0')
        self.assertGreater(data['checked'], time.time() - 60)

    def test_fetch_failure_still_stamps_checked(self):
        self.write_cache(checked=0, latest='0.17.0')
        with mock.patch.object(U, '_fetch_latest', return_value=None):
            U._refresh()
        data = U._load()
        self.assertEqual(data['latest'], '0.17.0')   # старое значение не теряем
        self.assertGreater(data['checked'], 0)


if __name__ == '__main__':
    unittest.main()
