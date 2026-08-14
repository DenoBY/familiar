import unittest
from unittest import mock

from test_overlay import FakeTab

import kittymock  # noqa: F401
import quit as Q


class FakeWindow:
    def __init__(self, tab):
        self._tab = tab

    def tabref(self):
        return self._tab


class FakeBoss:
    def __init__(self, tab):
        self.window_id_map = {7: FakeWindow(tab)}


class HandleResultTest(unittest.TestCase):
    def setUp(self):
        self.tab = FakeTab()
        self.boss = FakeBoss(self.tab)
        for name in ('write_snapshot', 'set_application_quit_request'):
            patch = mock.patch.object(Q, name)
            setattr(self, name, patch.start())
            self.addCleanup(patch.stop)

    def run_result(self, result):
        Q.handle_result([], result, 7, self.boss)

    def test_confirmed_quits(self):
        """Выход — только флагом: boss.quit() поверх него сбрасывает
        запрос обратно, и kitty остаётся жить (проверено вживую).
        """
        self.run_result({'action': 'quit'})
        self.set_application_quit_request.assert_called_once_with(
            Q.IMPERATIVE_CLOSE_REQUESTED)

    def test_confirmed_snapshots_before_leaving(self):
        self.run_result({'action': 'quit'})
        self.write_snapshot.assert_called_once_with(self.boss)

    def test_unwritable_snapshot_does_not_block_the_exit(self):
        self.write_snapshot.side_effect = OSError('read-only')
        self.run_result({'action': 'quit'})
        self.assertTrue(self.set_application_quit_request.called)

    def test_cancel_keeps_kitty_running(self):
        self.run_result({'action': 'cancel'})
        self.set_application_quit_request.assert_not_called()
        self.write_snapshot.assert_not_called()

    def test_layout_restored_in_both_cases(self):
        self.run_result({'action': 'cancel'})
        self.assertEqual(self.tab.gotos, ['splits:split_axis=horizontal'])

    def test_missing_result_only_restores_layout(self):
        self.run_result(None)
        self.set_application_quit_request.assert_not_called()
        self.assertEqual(self.tab.gotos, ['splits:split_axis=horizontal'])


if __name__ == '__main__':
    unittest.main()
