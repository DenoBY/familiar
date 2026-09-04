"""Слияние лент коммитов: порядок по времени и правило отсечки."""

import unittest

import kittymock  # noqa: F401
from modules.log.multi import Feed, merge_feeds, page_size, relative_age
from modules.vcs.workspace import Repo


def _commits(name: str, *stamps: int) -> list:
    return [{'sha': f'{name}{ts}', 'ts': ts, 'repo': f'/{name}', 'repo_name': name}
            for ts in stamps]


def _feed(name: str, *stamps: int, exhausted: bool = True) -> Feed:
    return Feed(Repo(f'/{name}', name), _commits(name, *stamps), exhausted)


class MergeTest(unittest.TestCase):

    def test_orders_by_time_across_repos(self):
        merged, done = merge_feeds([_feed('api', 300, 100), _feed('web', 200, 50)])
        self.assertEqual([c['ts'] for c in merged], [300, 200, 100, 50])
        self.assertTrue(done)

    def test_unfinished_feed_holds_back_older_commits(self):
        # api дочитан до 300, значит всё, что старше, ещё может прийти
        merged, done = merge_feeds([_feed('api', 500, 300, exhausted=False),
                                    _feed('web', 400, 200)])
        self.assertEqual([c['ts'] for c in merged], [500, 400, 300])
        self.assertFalse(done)

    def test_growing_a_feed_only_appends(self):
        feeds = [_feed('api', 500, 300, exhausted=False), _feed('web', 400, 200)]
        before, _ = merge_feeds(feeds)
        grown = [Feed(feeds[0].repo, feeds[0].commits + _commits('api', 250, 100), True),
                 feeds[1]]
        after, done = merge_feeds(grown)
        self.assertEqual([c['sha'] for c in after[:len(before)]],
                         [c['sha'] for c in before])
        self.assertEqual([c['ts'] for c in after], [500, 400, 300, 250, 200, 100])
        self.assertTrue(done)

    def test_empty_feed_does_not_block(self):
        merged, done = merge_feeds([_feed('api'), _feed('web', 100)])
        self.assertEqual([c['ts'] for c in merged], [100])
        self.assertTrue(done)

    def test_no_feeds(self):
        self.assertEqual(merge_feeds([]), ([], True))


class PageSizeTest(unittest.TestCase):

    def test_splits_batch_between_repos(self):
        self.assertEqual(page_size(3, 300), 100)

    def test_never_smaller_than_a_screenful(self):
        self.assertEqual(page_size(30, 300), 60)

    def test_single_repo_keeps_whole_batch(self):
        self.assertEqual(page_size(1, 300), 300)


class RelativeAgeTest(unittest.TestCase):

    def test_units(self):
        now = 1_000_000_000
        cases = [(30, 'now'), (300, '5m'), (7200, '2h'), (4 * 86400, '4d'),
                 (90 * 86400, '3mo'), (800 * 86400, '2y')]
        for delta, expected in cases:
            self.assertEqual(relative_age(now - delta, now), expected)

    def test_future_stamp_is_now(self):
        self.assertEqual(relative_age(1_000_000_100, 1_000_000_000), 'now')


if __name__ == '__main__':
    unittest.main()
