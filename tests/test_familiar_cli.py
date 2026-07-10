import contextlib
import importlib.machinery
import importlib.util
import io
import os
import re
import tempfile
import unittest


_TESTS = os.path.dirname(os.path.abspath(__file__))
_BIN = os.path.join(os.path.dirname(_TESTS), "bin", "familiar")

_spec = importlib.util.spec_from_loader(
    "familiar_cli", importlib.machinery.SourceFileLoader("familiar_cli", _BIN))
familiar = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(familiar)


def _run(argv):
    """Запуск CLI с подавленным stdout; возвращает напечатанное."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        familiar.main(argv)
    return out.getvalue()


class VersionTests(unittest.TestCase):
    def test_cli_version_matches_formula_tag(self):
        """Формулу при релизе бампят, а VERSION в CLI забывают —
        тогда brew ставит одну версию, а `familiar --version` врёт
        про другую.
        """
        formula = os.path.join(os.path.dirname(_TESTS), "Formula", "familiar.rb")
        with open(formula) as f:
            url = re.search(r'url ".*/tags/v([\d.]+)\.tar\.gz"', f.read())
        self.assertIsNotNone(url, "в формуле не нашёлся url релизного тега")
        self.assertEqual(familiar.VERSION, url.group(1))


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


class ThemeTests(unittest.TestCase):
    def test_default_theme_writes_nothing(self):
        # ghostty — дефолт: его палитру уже тянет terminal.conf,
        # а китам нечего сообщать через env
        conf = familiar.render_generated_conf(["review"], True)
        self.assertNotIn("FAMILIAR_THEME", conf)
        self.assertNotIn("look/ghostty.conf", conf)

    def test_theme_sets_env_and_palette_include(self):
        conf = familiar.render_generated_conf(["review"], True, "darcula")
        self.assertIn("env FAMILIAR_THEME=darcula", conf)
        self.assertIn("look/darcula.conf", conf)

    def test_palette_override_comes_after_terminal_conf(self):
        # terminal.conf тянет look/ghostty.conf; в kitty побеждает
        # последний include, поэтому палитра темы обязана идти следом
        conf = familiar.render_generated_conf(["review"], True, "darcula")
        self.assertLess(conf.index("terminal.conf"), conf.index("look/darcula.conf"))

    def test_theme_without_terminal_skips_the_palette(self):
        # без --terminal familiar не трогает внешний вид kitty,
        # но подсветку в китах тема задаёт всё равно
        conf = familiar.render_generated_conf(["review"], False, "darcula")
        self.assertIn("env FAMILIAR_THEME=darcula", conf)
        self.assertNotIn("look/darcula.conf", conf)

    def test_wired_theme_reads_back_what_was_written(self):
        for theme in familiar.THEMES:
            conf = familiar.render_generated_conf(["review"], True, theme)
            self.assertEqual(familiar.wired_theme(conf), theme)

    def test_themes_are_discovered_from_palette_files(self):
        self.assertIn("darcula", familiar.THEMES)
        self.assertEqual(familiar.THEMES[0], familiar.DEFAULT_THEME)

    def test_every_theme_has_a_look_file(self):
        # render_generated_conf подключает look/<тема>.conf — палитры
        # без терминальной половины быть не должно
        for theme in familiar.THEMES:
            path = familiar._theme_include_line(theme)[len("include "):]
            self.assertTrue(os.path.exists(path), path)


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


class SelectionTests(unittest.TestCase):
    def _resolve(self, *argv):
        args = familiar.build_parser().parse_args(["enable", *argv])
        return familiar._resolve_selection(args)

    def _parse_error(self, *argv):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                familiar.build_parser().parse_args(["enable", *argv])
        self.assertEqual(ctx.exception.code, 2)

    def test_all_selects_everything_with_terminal(self):
        self.assertEqual(self._resolve("--all"), (list(familiar.KITTENS), True))

    def test_kittens_selects_everything_without_terminal(self):
        self.assertEqual(self._resolve("--kittens"), (list(familiar.KITTENS), False))

    def test_kittens_plus_terminal_flag(self):
        self.assertEqual(self._resolve("--kittens", "--terminal"),
                         (list(familiar.KITTENS), True))

    def test_names_without_terminal(self):
        self.assertEqual(self._resolve("session", "log"), (["session", "log"], False))

    def test_names_plus_terminal_flag(self):
        self.assertEqual(self._resolve("review", "--terminal"), (["review"], True))

    def test_terminal_only_mode(self):
        self.assertEqual(self._resolve("--terminal"), ([], True))

    def test_empty_selection_rejected(self):
        with self.assertRaises(SystemExit):
            self._resolve()

    def test_unknown_kitten_rejected(self):
        with self.assertRaises(SystemExit):
            self._resolve("nope")

    def test_all_conflicts_with_names(self):
        self._parse_error("--all", "session")

    def test_all_conflicts_with_kittens(self):
        self._parse_error("--all", "--kittens")

    def test_kittens_conflicts_with_names(self):
        self._parse_error("--kittens", "session")


class WiredRootTests(unittest.TestCase):
    def test_root_from_kitten_map(self):
        conf = familiar.render_generated_conf(["session"], False)
        self.assertEqual(familiar.wired_root(conf), familiar.app_root())

    def test_root_from_terminal_only_conf(self):
        conf = familiar.render_generated_conf([], True)
        self.assertNotIn("cc_plugin=", conf)
        self.assertEqual(familiar.wired_root(conf), familiar.app_root())

    def test_unknown_content_returns_none(self):
        self.assertIsNone(familiar.wired_root(""))
        self.assertIsNone(familiar.wired_root("font_size 14\n# plugins/session.py\n"))

    def test_terminal_include_detected_regardless_of_root(self):
        self.assertTrue(familiar._has_terminal_include(
            "include /other/root/config/terminal.conf"))

    def test_terminal_include_ignores_comments_and_absence(self):
        self.assertFalse(familiar._has_terminal_include(
            "# include is described in config/terminal.conf"))
        self.assertFalse(familiar._has_terminal_include("include familiar.conf"))


class ConfigDirTests(unittest.TestCase):
    def setUp(self):
        self._env = {k: os.environ.get(k)
                     for k in ("KITTY_CONFIG_DIRECTORY", "XDG_CONFIG_HOME")}
        for k in self._env:
            os.environ.pop(k, None)
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_explicit_kitty_config_directory_wins(self):
        os.environ["KITTY_CONFIG_DIRECTORY"] = "/x/kitty-cfg"
        os.environ["XDG_CONFIG_HOME"] = "/x/xdg"
        self.assertEqual(familiar.kitty_config_dir(), "/x/kitty-cfg")

    def test_xdg_config_home_fallback(self):
        os.environ["XDG_CONFIG_HOME"] = "/x/xdg"
        self.assertEqual(familiar.kitty_config_dir(), "/x/xdg/kitty")

    def test_home_default(self):
        self.assertEqual(familiar.kitty_config_dir(),
                         os.path.expanduser("~/.config/kitty"))


class EndToEndTests(unittest.TestCase):
    """enable → status → disable → restore на временном
    каталоге конфига.
    """

    ORIGINAL = "font_size 14\nmap cmd+t new_tab\n"

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.config_dir = self.dir.name
        self.kitty_conf = os.path.join(self.config_dir, "kitty.conf")
        self.generated = os.path.join(self.config_dir, familiar.GENERATED_CONF)
        self.backup = self.kitty_conf + familiar.BACKUP_SUFFIX
        with open(self.kitty_conf, "w", encoding="utf-8") as f:
            f.write(self.ORIGINAL)
        self._old_env = os.environ.get("KITTY_CONFIG_DIRECTORY")
        os.environ["KITTY_CONFIG_DIRECTORY"] = self.config_dir
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        if self._old_env is None:
            os.environ.pop("KITTY_CONFIG_DIRECTORY", None)
        else:
            os.environ["KITTY_CONFIG_DIRECTORY"] = self._old_env

    def _read(self, path):
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_enable_status_disable_cycle(self):
        _run(["enable", "session"])

        conf = self._read(self.kitty_conf)
        self.assertTrue(conf.startswith(self.ORIGINAL))
        self.assertIn(familiar.MARKER_BEGIN, conf)
        self.assertIn(f"include {familiar.GENERATED_CONF}", conf)
        self.assertIn("cc_plugin=session", self._read(self.generated))
        self.assertEqual(self._read(self.backup), self.ORIGINAL)

        status = _run(["status"])
        self.assertIn("enabled:    yes", status)
        self.assertIn("kittens:    session", status)
        self.assertIn("terminal:   no", status)

        _run(["disable"])
        self.assertEqual(self._read(self.kitty_conf), self.ORIGINAL)
        self.assertFalse(os.path.exists(self.generated))
        self.assertIn("enabled:    no", _run(["status"]))

    def test_enable_is_idempotent(self):
        _run(["enable", "review"])
        first = self._read(self.kitty_conf)
        _run(["enable", "review"])
        self.assertEqual(self._read(self.kitty_conf), first)
        self.assertEqual(first.count(familiar.MARKER_BEGIN), 1)
        self.assertEqual(self._read(self.backup), self.ORIGINAL)

    def test_terminal_only_mode(self):
        _run(["enable", "--terminal", "-y"])
        generated = self._read(self.generated)
        self.assertIn("terminal.conf", generated)
        self.assertNotIn("cc_plugin=", generated)

        status = _run(["status"])
        self.assertIn("terminal:   yes", status)
        self.assertIn("kittens:    —", status)

    def test_status_terminal_detection_needs_exact_include_line(self):
        _run(["enable", "session"])
        # Упоминание terminal.conf в комментарии не должно
        # давать terminal: yes.
        with open(self.generated, "a", encoding="utf-8") as f:
            f.write("# include is described in config/terminal.conf\n")
        self.assertIn("terminal:   no", _run(["status"]))

    def test_backup_taken_once(self):
        self.assertEqual(familiar._backup_once(self.kitty_conf), self.backup)
        with open(self.kitty_conf, "w", encoding="utf-8") as f:
            f.write("changed\n")
        self.assertIsNone(familiar._backup_once(self.kitty_conf))
        self.assertEqual(self._read(self.backup), self.ORIGINAL)

    def test_restore_works_when_block_already_removed(self):
        _run(["enable", "session"])
        _run(["disable"])
        with open(self.kitty_conf, "a", encoding="utf-8") as f:
            f.write("junk\n")

        out = _run(["disable", "--restore"])
        self.assertIn("not enabled", out)
        self.assertIn("restored", out)
        self.assertEqual(self._read(self.kitty_conf), self.ORIGINAL)

    def test_disable_restore_reverts_original(self):
        _run(["enable", "--all", "-y"])
        _run(["disable", "--restore"])
        self.assertEqual(self._read(self.kitty_conf), self.ORIGINAL)

    def test_restore_without_backup_reports_it(self):
        os.remove(self.kitty_conf)
        out = _run(["disable", "--restore"])
        self.assertIn("no backup", out)

    def test_status_reports_wired_root_and_warns_on_mismatch(self):
        _run(["enable", "session", "--terminal", "-y"])
        with open(self.generated, encoding="utf-8") as f:
            conf = f.read()
        with open(self.generated, "w", encoding="utf-8") as f:
            f.write(conf.replace(familiar.app_root(), "/other/root"))

        status = _run(["status"])
        self.assertIn("wired root: /other/root", status)
        self.assertIn(f"app root:   {familiar.app_root()}", status)
        self.assertIn("terminal:   yes", status)
        self.assertIn("warning:", status)

    def test_status_has_no_warning_when_roots_match(self):
        _run(["enable", "session"])
        status = _run(["status"])
        self.assertIn(f"wired root: {familiar.app_root()}", status)
        self.assertNotIn("warning:", status)

    def test_status_without_familiar_has_no_wired_root(self):
        status = _run(["status"])
        self.assertIn("enabled:    no", status)
        self.assertNotIn("wired root:", status)

    def test_write_is_atomic_and_leaves_no_temp_files(self):
        familiar._write(self.kitty_conf, "new content\n")
        self.assertEqual(self._read(self.kitty_conf), "new content\n")
        leftovers = [n for n in os.listdir(self.config_dir) if n.startswith(".familiar-")]
        self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
