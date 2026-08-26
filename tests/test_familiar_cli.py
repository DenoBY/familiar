import contextlib
import importlib.machinery
import importlib.util
import io
import os
import re
import shutil
import tempfile
import unittest
from unittest import mock


_TESTS = os.path.dirname(os.path.abspath(__file__))
_BIN = os.path.join(os.path.dirname(_TESTS), "bin", "familiar")

# Иначе `status` в тестах ходил бы в GitHub за последним релизом.
os.environ["FAMILIAR_UPDATE_CHECK"] = "0"

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
        """Формулу при релизе бампят, а файл VERSION забывают —
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

    def test_kitten_opens_fullscreen_via_stack(self):
        conf = familiar.render_generated_conf(["review"], False)
        self.assertIn("@ goto_layout stack @ kitten ", conf)

    def test_close_kitten_takes_over_cmd_w_in_terminal_mode(self):
        conf = familiar.render_generated_conf(["session"], True)
        self.assertIn("cmd+w kitten " + familiar.plugins_dir() + "/close.py "
                      + familiar.plugins_dir() + "/close_ask.py", conf)

    def test_close_kitten_needs_the_terminal_config(self):
        # без splits.conf cmd+w панель не закрывает — и переопределять
        # тогда нечего
        self.assertNotIn("close.py", familiar.render_generated_conf(["session"], False))

    def test_cmd_w_block_declares_the_guards_after_the_kitten(self):
        """Из подходящих map kitty берёт последний объявленный, а
        безусловный подходит всегда — значит оба уточнения safety.conf
        (вопрос kitty `ask`, оверлеи китов) и точечное для самого
        вопроса close обязаны стоять НИЖЕ строки с китом.
        """
        conf = familiar.render_generated_conf(["session"], True)
        block = [ln for ln in conf.splitlines()
                 if ln.startswith("map") and "cmd+w" in ln]
        self.assertEqual(
            [ln.partition(" cmd+w ")[2] for ln in block],
            [f"kitten {familiar.plugins_dir()}/close.py"
             f" {familiar.plugins_dir()}/close_ask.py",
             "discard_event",          # cmdline:^ask$ — вопрос самой kitty
             "send_text all \\x03",    # любой оверлей кита — закрыть кит
             "discard_event"])         # var:cc_plugin=close — сам вопрос
        self.assertIn("cmdline:^ask$", block[1])
        self.assertIn("var:cc_plugin=close", block[3])

    def test_conditional_map_goes_after_the_unconditional_one(self):
        """Из подходящих map kitty берёт последний объявленный, а
        безусловный подходит всегда: стоя ниже, он перекрыл бы
        discard_event, и повторное нажатие снова открывало бы кит.
        """
        conf = familiar.render_generated_conf(["session"], False)
        lines = [ln for ln in conf.splitlines() if "cmd+shift+s " in ln]
        self.assertLess(next(i for i, ln in enumerate(lines) if "combine" in ln),
                        next(i for i, ln in enumerate(lines) if "discard_event" in ln))

    def test_review_gets_find_in_files_maps(self):
        conf = familiar.render_generated_conf(["review"], False)
        # ⌘⇧f работает только в фокусе review (проброс клавиши киту);
        # глобального маппинга нет — снаружи оверлея поиск не открыть
        self.assertIn("map --when-focus-on var:cc_plugin=review cmd+shift+f\n", conf)
        self.assertIn("map --when-focus-on var:cc_plugin=review cmd+shift+а\n", conf)
        self.assertNotIn("--search", conf)
        self.assertNotIn("map cmd+shift+f", conf)
        self.assertNotIn("map cmd+shift+а", conf)

    def test_clipboard_kittens_pass_cmd_f_through(self):
        conf = familiar.render_generated_conf(["log"], False)
        self.assertIn("map --when-focus-on var:cc_plugin=log cmd+f\n", conf)
        self.assertIn("map --when-focus-on var:cc_plugin=log cmd+а\n", conf)
        self.assertNotIn("cmd+f", familiar.render_generated_conf(["session"], False))


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


class RestoreTests(unittest.TestCase):
    def _resolve(self, *argv):
        args = familiar.build_parser().parse_args(["enable", *argv])
        return familiar._resolve_restore(args)

    def test_on_with_all(self):
        self.assertTrue(self._resolve("--all"))

    def test_off_for_a_plain_selection(self):
        self.assertFalse(self._resolve("session"))
        self.assertFalse(self._resolve("--kittens"))

    def test_explicit_flags_win(self):
        self.assertFalse(self._resolve("--all", "--no-restore-session"))
        self.assertTrue(self._resolve("session", "--restore-session"))

    def test_conf_wires_watcher_startup_session_and_key(self):
        conf = familiar.render_generated_conf(["session"], False, restore=True)
        session = familiar.restore_session_path()
        self.assertIn("watcher " + familiar.plugins_dir() + "/watchers/restore.py", conf)
        self.assertIn("startup_session " + session, conf)
        self.assertIn(f"map cmd+shift+t goto_session {session}", conf)

    def test_cyrillic_duplicate_for_the_restore_key(self):
        conf = familiar.render_generated_conf([], False, restore=True)
        self.assertIn("map cmd+shift+е goto_session", conf)

    def test_nothing_written_when_off(self):
        conf = familiar.render_generated_conf(["session"], True)
        self.assertNotIn("watcher ", conf)
        self.assertNotIn("startup_session", conf)

    def test_detected_regardless_of_root(self):
        self.assertTrue(familiar._has_restore(
            "watcher /other/root/plugins/watchers/restore.py"))
        self.assertFalse(familiar._has_restore(
            "# watcher /r/plugins/watchers/restore.py"))


class WiredRootTests(unittest.TestCase):
    def test_root_from_kitten_map(self):
        conf = familiar.render_generated_conf(["session"], False)
        self.assertEqual(familiar.wired_root(conf), familiar.app_root())

    def test_root_from_terminal_only_conf(self):
        conf = familiar.render_generated_conf([], True)
        # киты из списка не подключены; close идёт с терминал-конфигом
        self.assertEqual(set(re.findall(r"cc_plugin=(\w+)", conf)), {"close"})
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

    def test_enable_creates_the_startup_session_placeholder(self):
        """Без файла kitty пишет в лог «failed to read session file»
        при каждом старте, пока watcher не снимет первый снимок.
        """
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.config_dir}):
            _run(["enable", "session", "--restore-session"])
            self.assertTrue(os.path.exists(familiar.restore_session_path()))

    def test_disable_reports_snapshots_left_behind(self):
        """Удалять записи сеансов молча нельзя, оставлять молча — тоже:
        в дампах вывод терминала, а подобрать их уже некому.
        """
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.config_dir}):
            _run(["enable", "session", "--restore-session"])
            out = _run(["disable"])
        self.assertIn("window snapshots left on disk", out)
        self.assertIn(os.path.join(self.config_dir, "familiar", "restore"), out)

    def test_disable_stays_quiet_without_snapshots(self):
        _run(["enable", "session"])
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.config_dir}):
            self.assertNotIn("snapshots", _run(["disable"]))

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
        self.assertEqual(set(re.findall(r"cc_plugin=(\w+)", generated)), {"close"})

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


class LspTests(unittest.TestCase):
    """`familiar lsp`: CLI и кит обязаны видеть один и тот же реестр."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="famlsp_")
        self._backup = {k: os.environ.get(k)
                        for k in ("XDG_CONFIG_HOME", "XDG_CACHE_HOME")}
        os.environ["XDG_CONFIG_HOME"] = os.path.join(self.dir, "config")
        os.environ["XDG_CACHE_HOME"] = os.path.join(self.dir, "cache")
        registry, _install = familiar._lsp_modules()
        registry.reset_cache()
        self.registry = registry

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)
        for key, value in self._backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.registry.reset_cache()

    def test_status_lists_registry_languages(self):
        out = _run(["lsp", "status"])
        self.assertIn("php", out)
        self.assertIn("intelephense", out)

    def test_status_survives_without_any_server(self):
        out = _run(["lsp", "status"])
        self.assertIn("familiar lsp install", out)

    def test_status_prints_template_for_unknown_language(self):
        out = _run(["lsp", "status", "cobol"])
        self.assertIn("server cobol", out)
        self.assertIn("extensions", out)

    def test_status_shows_config_paths(self):
        out = _run(["lsp", "status"])
        self.assertIn(self.registry.builtin_path(), out)
        self.assertIn("not present", out)      # пользовательского ещё нет

    def test_cli_and_kitten_share_one_registry(self):
        # конфиг разбирает общий модуль: две копии разошлись бы молча
        conf = self.registry.user_path()
        os.makedirs(os.path.dirname(conf), exist_ok=True)
        with open(conf, "w") as f:
            f.write("server cobol\n  extensions .cbl\n  command cobol-ls\n")
        self.registry.reset_cache()
        self.assertIn("cobol", _run(["lsp", "status"]))
        self.assertEqual(self.registry.for_path("x.cbl"), "cobol")

    def test_install_reports_unknown_language(self):
        self.assertIn("not in the registry", _run(["lsp", "install", "cobol"]))

    def test_install_survives_a_block_without_command(self):
        # блок в пользовательском конфиге бывает неполным: ставить
        # нечего, но и падать на пустом argv команда не должна
        conf = self.registry.user_path()
        os.makedirs(os.path.dirname(conf), exist_ok=True)
        with open(conf, "w") as f:
            f.write("server cobol\n  extensions .cbl\n  install npm cobol-ls\n")
        self.registry.reset_cache()
        self.assertIn("not in the registry", _run(["lsp", "install", "cobol"]))

    def test_warm_does_not_call_a_dead_server_indexed(self):
        # сервер, упавший на старте, возвращает штатный Progress с
        # 'failed' — «indexed in 0s» на это было бы прямой неправдой
        from modules.lsp import session as lsp_session
        original = lsp_session.warm_up
        lsp_session.warm_up = lambda *a, **kw: lsp_session.Progress(
            'failed', -1, '', 0.0, 0)
        try:
            out = _run(["lsp", "warm", "php"])
        finally:
            lsp_session.warm_up = original
        self.assertNotIn("indexed", out)
        self.assertIn("php", out)

    def test_clean_without_cache_says_so(self):
        self.assertIn("nothing to clean", _run(["lsp", "clean", "-y"]))

    def test_clean_removes_indexes(self):
        indexes = self.registry.index_home()
        os.makedirs(indexes, exist_ok=True)
        with open(os.path.join(indexes, "junk.bin"), "wb") as f:
            f.write(b"x" * 10)
        _run(["lsp", "clean", "-y"])
        self.assertFalse(os.path.isdir(indexes))

    def test_clean_keeps_installed_servers(self):
        # индексы наживаются заново, а серверы ставили отдельной
        # командой — сносить их никто не просил
        servers = self.registry.server_home()
        os.makedirs(os.path.join(servers, "bin"), exist_ok=True)
        os.makedirs(self.registry.index_home(), exist_ok=True)
        _run(["lsp", "clean", "-y"])
        self.assertTrue(os.path.isdir(servers))

    def test_enable_offers_servers_without_asking_when_not_a_tty(self):
        # без tty вопрос повесил бы и brew test, и CI
        with tempfile.TemporaryDirectory() as conf:
            with mock.patch.dict(os.environ, {"KITTY_CONFIG_DIRECTORY": conf}):
                out = _run(["enable", "review"])
        self.assertIn("familiar lsp install", out)

    def test_enable_can_skip_the_offer(self):
        with tempfile.TemporaryDirectory() as conf:
            with mock.patch.dict(os.environ, {"KITTY_CONFIG_DIRECTORY": conf}):
                out = _run(["enable", "review", "--no-lsp"])
        self.assertNotIn("go-to-definition", out)

    def test_session_kitten_alone_gets_no_offer(self):
        with tempfile.TemporaryDirectory() as conf:
            with mock.patch.dict(os.environ, {"KITTY_CONFIG_DIRECTORY": conf}):
                out = _run(["enable", "session"])
        self.assertNotIn("go-to-definition", out)
