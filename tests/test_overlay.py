import io
import unittest
from contextlib import redirect_stdout
from unittest import mock

import kittymock  # noqa: F401
from modules import overlay
from modules.overlay import mark_overlay, restore_layout, run_loop


class MarkOverlayTest(unittest.TestCase):
    def test_emits_osc_setuservar_base64(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            mark_overlay('session')
        # c2Vzc2lvbg== = base64('session'); OSC 1337 SetUserVar,
        # затем OSC 2 — заголовок окна; терминатор BEL
        self.assertEqual(buf.getvalue(),
                         '\033]1337;SetUserVar=cc_plugin=c2Vzc2lvbg==\007'
                         '\033]2;Session\007')

    def test_value_differs_per_plugin(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            mark_overlay('review')
        self.assertEqual(buf.getvalue(),
                         '\033]1337;SetUserVar=cc_plugin=cmV2aWV3\007'
                         '\033]2;Review\007')


class FakeLayout:
    def __init__(self, name):
        self.name = name


class FakeTab:
    def __init__(self, current='stack', last_used='splits:split_axis=horizontal',
                 enabled=('splits:split_axis=horizontal', 'stack')):
        self.current_layout = FakeLayout(current)
        self._last_used_layout = last_used
        self.enabled_layouts = list(enabled)
        self.gotos = []

    def goto_layout(self, name):
        self.gotos.append(name)


class FakeWindow:
    def __init__(self, tab):
        self._tab = tab

    def tabref(self):
        return self._tab


class FakeBoss:
    def __init__(self, tab):
        window = FakeWindow(tab) if tab is not None else None
        self.window_id_map = {7: window} if window else {}


class RestoreLayoutTest(unittest.TestCase):
    def test_restores_last_used_layout(self):
        tab = FakeTab()
        restore_layout(FakeBoss(tab), 7)
        self.assertEqual(tab.gotos, ['splits:split_axis=horizontal'])

    def test_noop_outside_stack(self):
        # пользователь сам ушёл из stack, пока кит был открыт
        tab = FakeTab(current='splits')
        restore_layout(FakeBoss(tab), 7)
        self.assertEqual(tab.gotos, [])

    def test_clobbered_last_used_falls_back_to_enabled(self):
        # след вытеснения кита китом: повторный goto_layout stack
        # затёр _last_used_layout
        tab = FakeTab(last_used='stack')
        restore_layout(FakeBoss(tab), 7)
        self.assertEqual(tab.gotos, ['splits:split_axis=horizontal'])

    def test_only_stack_enabled_is_noop(self):
        tab = FakeTab(last_used=None, enabled=('stack',))
        restore_layout(FakeBoss(tab), 7)
        self.assertEqual(tab.gotos, [])

    def test_source_window_gone_is_noop(self):
        restore_layout(FakeBoss(None), 7)   # не должно упасть


class RunLoopTest(unittest.TestCase):
    def test_handler_reaches_the_loop(self):
        handler = object()
        with mock.patch.object(overlay, 'Loop') as loop:
            run_loop(handler)
        loop.return_value.loop.assert_called_once_with(handler)

    def test_torn_frame_tail_does_not_kill_the_kitten(self):
        # хвост кадра, обрезанный os.writev посреди многобайтового
        # символа: kitty декодирует его строгим utf-8 уже после того,
        # как выбор сделан, и без перехвата действие теряется
        torn = UnicodeDecodeError('utf-8', b'\x80', 0, 1, 'invalid start byte')
        with mock.patch.object(overlay, 'Loop') as loop:
            loop.return_value.loop.side_effect = torn
            run_loop(object())

    def test_other_errors_still_surface(self):
        with mock.patch.object(overlay, 'Loop') as loop:
            loop.return_value.loop.side_effect = RuntimeError('boom')
            with self.assertRaises(RuntimeError):
                run_loop(object())


if __name__ == '__main__':
    unittest.main()
