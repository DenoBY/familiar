import shlex
import unittest

import kittymock  # noqa: F401
import modules.restore.sessionfile as Sf


# Так строку пишет сама kitty: служебный аргумент первым, наш токен
# среди флагов, команда окна в хвосте.
LAUNCH = 'launch \'kitty-unserialize-data={"id": 1}\' --var=cc_restore=w1 zsh -l'


def patch(line, patches):
    return Sf.apply_patches([line], patches)[0]


class TestApplyPatches(unittest.TestCase):
    def test_inserts_run_env(self):
        out = patch(LAUNCH, {'w1': {'run': 'claude --resume x', 'cwd': None}})
        args = shlex.split(out)
        self.assertIn(f'--env={Sf.RUN_ENV}=claude --resume x', args)

    def test_flags_go_right_after_the_service_argument(self):
        out = patch(LAUNCH, {'w1': {'run': 'htop', 'cwd': None}})
        args = shlex.split(out)
        self.assertEqual(args[2], f'--env={Sf.RUN_ENV}=htop')

    def test_service_argument_stays_first(self):
        out = patch(LAUNCH, {'w1': {'run': 'htop', 'cwd': None}})
        self.assertTrue(shlex.split(out)[1].startswith('kitty-unserialize-data='))

    def test_token_is_not_carried_into_the_restored_window(self):
        out = patch(LAUNCH, {'w1': {'run': 'htop', 'cwd': None}})
        self.assertNotIn('cc_restore', out)

    def test_cwd_replaces_the_serialized_one(self):
        line = 'launch --cwd=/old --var=cc_restore=w1 zsh'
        out = patch(line, {'w1': {'run': 'claude --resume x', 'cwd': '/new'}})
        args = shlex.split(out)
        self.assertIn('--cwd=/new', args)
        self.assertNotIn('--cwd=/old', args)

    def test_cwd_added_when_kitty_wrote_none(self):
        out = patch(LAUNCH, {'w1': {'run': 'claude --resume x', 'cwd': '/proj'}})
        self.assertIn('--cwd=/proj', shlex.split(out))

    def test_command_with_spaces_survives_a_reparse(self):
        run = "cat '/tmp/s b.txt'; nvim 'a b.py'"
        out = patch(LAUNCH, {'w1': {'run': run, 'cwd': None}})
        self.assertIn(f'--env={Sf.RUN_ENV}={run}', shlex.split(out))

    def test_window_own_command_is_dropped(self):
        """Окно кита session создано с командой `zsh -c 'exec claude
        --continue'`. Она перекрыла бы RUN_ENV (его исполняет shell
        integration, а `zsh -c …` её инициализацию не проходит), и
        claude поднялся бы последней сессией проекта вместо своей.
        """
        line = ("launch --var=cc_restore=w1 --type=overlay-main "
                "/bin/zsh -l -i -c 'exec claude --continue'")
        out = patch(line, {'w1': {'run': 'claude --resume sid-1', 'cwd': None}})
        self.assertNotIn('--continue', out)
        args = shlex.split(out)
        self.assertIn('--type=overlay-main', args)
        self.assertIn(f'--env={Sf.RUN_ENV}=claude --resume sid-1', args)
        self.assertNotIn('/bin/zsh', args)

    def test_window_command_kept_without_a_patch(self):
        line = "launch --title=x /bin/zsh -l -i -c 'exec htop'"
        self.assertEqual(patch(line, {'w1': {'run': 'nvim'}}), line)

    def test_stale_env_replaced_not_duplicated(self):
        """Восстановленное окно помнит, с каким --env его запустили, и
        kitty сериализует его обратно: без чистки команды копились бы
        и восстановление выполняло устаревшую.
        """
        line = (f'launch \'--env={Sf.RUN_ENV}=cat /old/dump.txt\' '
                '--var=cc_restore=w1 zsh')
        out = patch(line, {'w1': {'run': 'htop', 'cwd': None}})
        envs = [a for a in shlex.split(out) if a.startswith(f'--env={Sf.RUN_ENV}=')]
        self.assertEqual(envs, [f'--env={Sf.RUN_ENV}=htop'])

    def test_stale_env_dropped_even_without_a_patch(self):
        # в окне больше нечего восстанавливать — старая команда должна
        # уйти вместе с ним
        line = f'launch \'--env={Sf.RUN_ENV}=cat /old/dump.txt\' zsh'
        self.assertNotIn(Sf.RUN_ENV, patch(line, {}))

    def test_untouched_without_a_matching_token(self):
        self.assertEqual(patch(LAUNCH, {'w9': {'run': 'htop', 'cwd': None}}), LAUNCH)

    def test_untouched_without_patches(self):
        self.assertEqual(Sf.apply_patches([LAUNCH], {}), [LAUNCH])

    def test_other_directives_are_left_alone(self):
        lines = ['new_tab', 'cd /x', 'layout splits', 'focus_tab 0']
        self.assertEqual(Sf.apply_patches(lines, {'w1': {'run': 'htop'}}), lines)

    def test_indent_preserved(self):
        out = patch('  ' + LAUNCH, {'w1': {'run': 'htop', 'cwd': None}})
        self.assertTrue(out.startswith('  launch'))

    def test_unparsable_line_is_left_alone(self):
        line = "launch --var=cc_restore=w1 'unbalanced"
        self.assertEqual(patch(line, {'w1': {'run': 'htop'}}), line)


if __name__ == '__main__':
    unittest.main()
