import unittest
from unittest import mock

import kittymock  # noqa: F401
import modules.quit.screen as Qs
from kittymock import KeyEvent, draw_text, wire


class TestLiveSessions(unittest.TestCase):
    def sessions(self, running):
        with mock.patch.object(Qs, 'running_sessions', return_value=running):
            return Qs.live_sessions()

    def test_project_name_from_cwd(self):
        res = self.sessions({'s1': {'cwd': '/Users/d/Projects/kitty', 'status': 'busy'}})
        self.assertEqual(res, [('kitty', 'busy')])

    def test_trailing_slash_and_missing_status(self):
        res = self.sessions({'s1': {'cwd': '/Users/d/proj/'}})
        self.assertEqual(res, [('proj', 'idle')])

    def test_sorted_for_a_stable_screen(self):
        res = self.sessions({'a': {'cwd': '/x/zeta'}, 'b': {'cwd': '/x/alpha'}})
        self.assertEqual([name for name, _ in res], ['alpha', 'zeta'])

    def test_no_sessions(self):
        self.assertEqual(self.sessions({}), [])


class TestBody(unittest.TestCase):
    def plain(self, sessions, width=80):
        return [text for text, _ in Qs.body(sessions, width)]

    def test_question_comes_first(self):
        self.assertEqual(self.plain([])[0], 'Quit kitty?')

    def test_says_windows_come_back(self):
        self.assertIn('Windows and tabs come back on the next start.', self.plain([]))

    def test_sessions_are_counted_and_listed(self):
        lines = self.plain([('kitty', 'busy'), ('wiki', 'idle')])
        self.assertIn('2 Claude Code sessions running:', lines)
        self.assertIn('  kitty · busy', lines)

    def test_single_session_is_not_pluralized(self):
        self.assertIn('1 Claude Code session running:', self.plain([('kitty', 'busy')]))

    def test_long_list_collapses_into_a_counter(self):
        sessions = [(f'p{i}', 'idle') for i in range(Qs.MAX_LISTED + 3)]
        lines = self.plain(sessions)
        listed = [ln for ln in lines if ln.startswith('  p')]
        self.assertEqual(len(listed), Qs.MAX_LISTED)
        self.assertIn('  … and 3 more sessions', lines)

    def test_long_name_is_cut_but_status_survives(self):
        line = self.plain([('a' * 200, 'busy')], width=30)[3]
        self.assertTrue(line.endswith(' · busy'))
        self.assertIn('…', line)

    def test_no_session_block_without_sessions(self):
        self.assertFalse(any('Claude Code' in ln for ln in self.plain([])))


class TestScreen(unittest.TestCase):
    def screen(self, sessions=()):
        h = wire(Qs.QuitScreen(), rows=24, cols=80)
        h.sessions = list(sessions)
        h.start_quit_confirm()
        return h

    def test_buttons_match_the_other_kittens(self):
        text = draw_text(self.screen())
        self.assertIn('│ Yes │', text)
        self.assertIn('│ No │', text)

    def test_question_and_sessions_are_drawn(self):
        text = draw_text(self.screen([('kitty', 'busy')]))
        self.assertIn('Quit kitty?', text)
        self.assertIn('kitty · ', text)

    def test_screen_fits_its_rows(self):
        h = self.screen([(f'p{i}', 'idle') for i in range(4)])
        self.assertLessEqual(len(h.out), h.screen_size.rows)

    def test_enter_confirms(self):
        h = self.screen()
        h.on_key(KeyEvent('ENTER'))
        self.assertTrue(h.confirmed)
        self.assertEqual(h.quits, [0])

    def test_escape_cancels(self):
        h = self.screen()
        h.on_key(KeyEvent('ESCAPE'))
        self.assertFalse(h.confirmed)
        self.assertEqual(h.quits, [0])

    def test_y_confirms_n_cancels(self):
        h = self.screen()
        h.on_text('y')
        self.assertTrue(h.confirmed)
        h = self.screen()
        h.on_text('n')
        self.assertFalse(h.confirmed)

    def test_russian_layout_keys(self):
        # физические y/n на ЙЦУКЕН дают «н»/«т»
        h = self.screen()
        h.on_text('н')
        self.assertTrue(h.confirmed)
        h = self.screen()
        h.on_text('т')
        self.assertFalse(h.confirmed)

    def test_arrow_moves_focus_to_no(self):
        h = self.screen()
        h.on_key(KeyEvent('RIGHT'))
        h.on_key(KeyEvent('ENTER'))
        self.assertFalse(h.confirmed)

    def test_ctrl_c_cancels_instead_of_quitting_kitty(self):
        """У оверлеев ⌃c значит «закрыть кит», здесь бы значил «выйти
        из kitty» — для аварийной клавиши слишком много власти.
        """
        h = self.screen()
        h.on_text('\x03')
        self.assertFalse(h.confirmed)
        self.assertEqual(h.quits, [0])

    def test_interrupt_cancels(self):
        h = self.screen()
        h.on_interrupt()
        self.assertFalse(h.confirmed)

    def test_other_keys_do_nothing(self):
        h = self.screen()
        h.on_text('x')
        self.assertEqual(h.quits, [])


if __name__ == '__main__':
    unittest.main()
