import unittest

import kittymock  # noqa: F401
import modules.session.util as U


class TestHumanAge(unittest.TestCase):
    def test_just_now(self):
        self.assertEqual(U.human_age(0), 'just now')
        self.assertEqual(U.human_age(30), 'just now')

    def test_minutes(self):
        self.assertEqual(U.human_age(90), '1m ago')
        self.assertEqual(U.human_age(59 * 60), '59m ago')

    def test_hours(self):
        self.assertEqual(U.human_age(3600), '1h ago')
        self.assertEqual(U.human_age(5 * 3600), '5h ago')

    def test_days(self):
        self.assertEqual(U.human_age(90000), '1d ago')

    def test_months(self):
        self.assertEqual(U.human_age(5_000_000), '1mo ago')


if __name__ == '__main__':
    unittest.main()
