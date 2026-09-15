import random
import string
import time
import unittest

import kittymock  # noqa: F401
from modules.vcs.fuzzy import EXACT, FUZZY, PREFIX, buffer_words, match


def _best(query, candidates):
    scored = [(c, match(query, c)) for c in candidates]
    scored = [(c, m) for c, m in scored if m is not None]
    return [c for c, m in sorted(scored, key=lambda cm: (cm[1].tier, -cm[1].score))]


class MatchTest(unittest.TestCase):
    def test_tiers(self):
        self.assertEqual(match('where', 'where').tier, EXACT)
        self.assertEqual(match('whe', 'whereIn').tier, PREFIX)
        self.assertEqual(match('wIn', 'whereIn').tier, FUZZY)

    def test_not_a_subsequence(self):
        self.assertIsNone(match('xyz', 'whereIn'))

    def test_first_letter_must_start_a_word(self):
        self.assertIsNone(match('ab', 'grab'))
        self.assertIsNotNone(match('ab', 'xAb'))
        self.assertIsNotNone(match('na', '__name__'))
        self.assertIsNotNone(match('us', '$users'))

    def test_camel_humps_beat_scattered_letters(self):
        self.assertEqual(_best('gtf', ['getTextField', 'gotofile']),
                         ['getTextField', 'gotofile'])

    def test_consecutive_beats_gapped(self):
        self.assertEqual(_best('str', ['s_t_r_x', 'strlen'])[0], 'strlen')

    def test_case_is_a_tie_breaker(self):
        self.assertGreater(match('User', 'User').score, match('User', 'user').score)

    def test_positions_for_highlight(self):
        self.assertEqual(match('wI', 'whereIn').positions, (0, 5))
        self.assertEqual(match('whe', 'whereIn').positions, (0, 1, 2))

    def test_empty_query_matches_everything(self):
        self.assertEqual(match('', 'anything').tier, FUZZY)

    def test_filter_text_with_prefix_char(self):
        # ts: пункт члена фильтруется вместе с точкой
        self.assertEqual(match('.fo', '.foo').tier, PREFIX)

    def test_fast_enough_for_big_lists(self):
        rnd = random.Random(7)
        words = [''.join(rnd.choice(string.ascii_letters + '_') for _ in range(rnd.randint(4, 30)))
                 for _ in range(5000)]
        started = time.perf_counter()
        for query in ('g', 'ge', 'get', 'getT'):
            [match(query, w) for w in words]
        # грубая граница: четыре нажатия по 5000 пунктов — заметно
        # меньше секунды даже на медленной машине CI
        self.assertLess(time.perf_counter() - started, 1.0)


class BufferWordsTest(unittest.TestCase):
    def test_nearest_first_and_unique(self):
        lines = ['alpha beta', 'gamma', 'caret', 'beta delta']
        self.assertEqual(buffer_words(lines, 2, 'caret'), ['beta', 'delta', 'gamma', 'alpha'])

    def test_short_words_and_non_identifiers_skipped(self):
        self.assertEqual(buffer_words(['if x == 42: привет $users'], 0, ''), ['$users'])
