import importlib.machinery
import importlib.util
import os
import shutil
import stat
import tempfile
import unittest
from unittest import mock

import kittymock  # noqa: F401
import modules.restore.store as St


_TESTS = os.path.dirname(os.path.abspath(__file__))
_BIN = os.path.join(os.path.dirname(_TESTS), 'bin', 'familiar')


class TmpStateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='ccrestore_')
        self.env = mock.patch.dict(os.environ, {'XDG_STATE_HOME': self.tmp})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestPaths(TmpStateTest):
    def test_state_dir_follows_xdg(self):
        self.assertEqual(St.state_dir(), os.path.join(self.tmp, 'familiar', 'restore'))

    def test_last_session_path_is_stable(self):
        first = St.last_session_path()
        St.publish(St.new_snapshot_dir())
        self.assertEqual(St.last_session_path(), first)

    def test_cli_mirrors_the_same_path(self):
        """bin/familiar запекает путь в kitty.conf, не импортируя
        пакет китов, — разъехавшись, они дали бы startup_session,
        указывающий мимо снимков.
        """
        spec = importlib.util.spec_from_loader(
            'familiar_store_check',
            importlib.machinery.SourceFileLoader('familiar_store_check', _BIN))
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        self.assertEqual(cli.restore_session_path(), St.last_session_path())


class TestSnapshotDirs(TmpStateTest):
    def test_numbering_grows(self):
        self.assertEqual(os.path.basename(St.new_snapshot_dir()), '0001')
        self.assertEqual(os.path.basename(St.new_snapshot_dir()), '0002')

    def test_taken_number_is_skipped(self):
        # второй инстанс kitty пишет в тот же каталог
        root = os.path.join(St.state_dir(), 'snapshots')
        os.makedirs(os.path.join(root, '0001'))
        self.assertEqual(os.path.basename(St.new_snapshot_dir()), '0002')

    def test_directory_is_private(self):
        mode = stat.S_IMODE(os.stat(St.new_snapshot_dir()).st_mode)
        self.assertEqual(mode, 0o700)

    def test_stray_names_ignored(self):
        root = os.path.join(St.state_dir(), 'snapshots')
        os.makedirs(os.path.join(root, 'junk'))
        self.assertEqual(os.path.basename(St.new_snapshot_dir()), '0001')


class TestWriteText(TmpStateTest):
    def test_file_is_private(self):
        # в снимке вывод терминала — читать может только хозяин
        path = os.path.join(St.new_snapshot_dir(), 'x.txt')
        St.write_text(path, 'data')
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)

    def test_overwrites_without_leftovers(self):
        path = os.path.join(St.new_snapshot_dir(), 'x.txt')
        St.write_text(path, 'long content')
        St.write_text(path, 'short')
        with open(path) as f:
            self.assertEqual(f.read(), 'short')


class TestPublish(TmpStateTest):
    def _write(self, snapshot_dir, text):
        St.write_text(os.path.join(snapshot_dir, St.SESSION_NAME), text)

    def test_last_points_at_the_new_snapshot(self):
        first = St.new_snapshot_dir()
        self._write(first, 'one')
        St.publish(first)
        second = St.new_snapshot_dir()
        self._write(second, 'two')
        St.publish(second)
        with open(St.last_session_path()) as f:
            self.assertEqual(f.read(), 'two')

    def test_replaces_a_real_directory_left_in_place(self):
        # familiar enable кладёт заглушку обычной папкой
        link = os.path.join(St.state_dir(), St.LAST)
        os.makedirs(link)
        St.write_text(os.path.join(link, St.SESSION_NAME), '')
        snapshot = St.new_snapshot_dir()
        self._write(snapshot, 'real')
        St.publish(snapshot)
        self.assertTrue(os.path.islink(link))
        with open(St.last_session_path()) as f:
            self.assertEqual(f.read(), 'real')


class TestRotate(TmpStateTest):
    def test_keeps_the_last_n(self):
        for _ in range(5):
            St.new_snapshot_dir()
        St.rotate(keep=2)
        root = os.path.join(St.state_dir(), 'snapshots')
        self.assertEqual(sorted(os.listdir(root)), ['0004', '0005'])

    def test_nothing_to_rotate(self):
        St.rotate(keep=10)   # каталога ещё нет — не должно падать

    def test_keep_zero_clears_everything(self):
        St.new_snapshot_dir()
        St.rotate(keep=0)
        self.assertEqual(os.listdir(os.path.join(St.state_dir(), 'snapshots')), [])

    def test_publish_rotates(self):
        for _ in range(St.KEEP + 3):
            St.publish(St.new_snapshot_dir())
        root = os.path.join(St.state_dir(), 'snapshots')
        self.assertEqual(len(os.listdir(root)), St.KEEP)


if __name__ == '__main__':
    unittest.main()
