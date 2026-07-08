import importlib.util
import os
import unittest


_TESTS = os.path.dirname(os.path.abspath(__file__))
_BIN = os.path.join(os.path.dirname(_TESTS), "bin", "familiar")

_spec = importlib.util.spec_from_loader(
    "familiar_cli", importlib.machinery.SourceFileLoader("familiar_cli", _BIN))
familiar = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(familiar)


class RenderTests(unittest.TestCase):
    def test_kittens_only_has_no_terminal_include(self):
        conf = familiar.render_generated_conf(["session", "review", "log"], False)
        self.assertNotIn("terminal.conf", conf)
        self.assertIn("cc_plugin=session", conf)
        self.assertIn("plugins/session.py", conf)

    def test_terminal_mode_includes_terminal_conf(self):
        conf = familiar.render_generated_conf(["session"], True)
        self.assertIn("terminal.conf", conf)
        self.assertIn("include ", conf)

    def test_canonical_order_regardless_of_input(self):
        conf = familiar.render_generated_conf(["log", "session"], False)
        self.assertLess(conf.index("cc_plugin=session"), conf.index("cc_plugin=log"))

    def test_clipboard_unmaps_only_for_review_and_log(self):
        self.assertNotIn("cmd+c", familiar.render_generated_conf(["session"], False))
        self.assertIn("cmd+shift+c", familiar.render_generated_conf(["review"], False))

    def test_cyrillic_duplicates_present(self):
        conf = familiar.render_generated_conf(["session"], False)
        self.assertIn("cmd+shift+ы", conf)


class BlockTests(unittest.TestCase):
    def test_insert_appends_block(self):
        out = familiar.upsert_managed_block("font_size 14\n", "include familiar.conf")
        self.assertIn(familiar.MARKER_BEGIN, out)
        self.assertIn("include familiar.conf", out)
        self.assertTrue(out.startswith("font_size 14\n"))

    def test_insert_into_empty(self):
        out = familiar.upsert_managed_block("", "include familiar.conf")
        self.assertIn(familiar.MARKER_BEGIN, out)

    def test_upsert_is_idempotent(self):
        once = familiar.upsert_managed_block("x\n", "include familiar.conf")
        twice = familiar.upsert_managed_block(once, "include familiar.conf")
        self.assertEqual(once, twice)
        self.assertEqual(twice.count(familiar.MARKER_BEGIN), 1)

    def test_upsert_replaces_existing_include(self):
        once = familiar.upsert_managed_block("x\n", "include a.conf")
        updated = familiar.upsert_managed_block(once, "include b.conf")
        self.assertIn("include b.conf", updated)
        self.assertNotIn("include a.conf", updated)

    def test_remove_restores_original(self):
        original = "font_size 14\nmap cmd+t new_tab\n"
        with_block = familiar.upsert_managed_block(original, "include familiar.conf")
        cleaned, found = familiar.remove_managed_block(with_block)
        self.assertTrue(found)
        self.assertEqual(cleaned, original)

    def test_remove_when_absent(self):
        cleaned, found = familiar.remove_managed_block("font_size 14\n")
        self.assertFalse(found)
        self.assertEqual(cleaned, "font_size 14\n")

    def test_unterminated_block_raises(self):
        with self.assertRaises(ValueError):
            familiar.remove_managed_block(familiar.MARKER_BEGIN + "\ninclude x\n")


if __name__ == "__main__":
    unittest.main()
