#!/usr/bin/env python3
"""Генератор демо-стенда для скриншотов familiar.

Создаёт две площадки:
- <dir>/repo — git-проект «sundial» с историей, ветками и
  незакоммиченными правками (для китов review и log);
- <dir>/claude — фейковое хранилище Claude Code (для session).

Скриншоты снимаются из отдельного инстанса kitty, запущенного с
CLAUDE_CONFIG_DIR=<dir>/claude — команда печатается в конце.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone


# ---------------------------------------------------------------
# Исходники демо-проекта «sundial». Версии (V1, V2, …) — снимки
# файла по ходу истории коммитов; *_WT — незакоммиченное состояние
# рабочего дерева.
# ---------------------------------------------------------------

PYPROJECT = '''\
[project]
name = "sundial"
version = "0.2.0"
description = "Sunrise, sunset and moon phase calculations"
requires-python = ">=3.11"
'''

INIT_V1 = '''\
"""sundial — sunrise, sunset and moon phases."""

__version__ = '0.1.0'
'''

INIT_V2 = INIT_V1.replace('0.1.0', '0.2.0')

README_V1 = '''\
# sundial

Sunrise, sunset and moon phase calculations in pure Python.
'''

README_V2 = README_V1 + '''
## Usage

```sh
python -m sundial.cli 38.7 -9.1 --date 2026-06-01
```
'''

README_V3 = README_V2 + '''
## Moon phases

```python
from sundial.moon import phase_name
phase_name(date.today())
```
'''

UTILS_V1 = '''\
"""Small numeric helpers shared across sundial."""


def normalize_degrees(angle: float) -> float:
    """Wrap an angle into [0, 360)."""
    return angle % 360.0


def clamp(value: float, lo: float = -1.0, hi: float = 1.0) -> float:
    """Clamp a value into [lo, hi]; guards acos domain errors."""
    return max(lo, min(hi, value))
'''

UTILS_V2 = '''\
"""Small numeric helpers shared across sundial."""

from datetime import time


def normalize_degrees(angle: float) -> float:
    """Wrap an angle into [0, 360)."""
    return angle % 360.0


def clamp(value: float, lo: float = -1.0, hi: float = 1.0) -> float:
    """Clamp a value into [lo, hi]; guards acos domain errors."""
    return max(lo, min(hi, value))


def minutes_to_time(minutes: float) -> time:
    """Minutes since UTC midnight -> time, wrapping over 24h."""
    total = int(minutes) % (24 * 60)
    return time(total // 60, total % 60)
'''

UTILS_STAGED = UTILS_V2.replace(
    'from datetime import time',
    'import math\nfrom datetime import time',
) + '''

def frac_year(day_of_year: int, hours: float = 12.0) -> float:
    """Fractional year in radians for the NOAA equations."""
    return 2 * math.pi / 365 * (day_of_year - 1 + (hours - 12) / 24)
'''

CORE_V1 = '''\
"""Solar position math: declination, hour angle, sunrise and sunset."""

import math
from dataclasses import dataclass
from datetime import date, time


@dataclass(frozen=True)
class Observer:
    """A point on Earth in degrees; east and north are positive."""

    latitude: float
    longitude: float


def declination(day_of_year: int) -> float:
    """Solar declination in degrees for a day of the year."""
    return -23.44 * math.cos(math.radians(360.0 / 365.0 * (day_of_year + 10)))


def hour_angle(latitude: float, decl: float) -> float:
    """Half the day arc in degrees."""
    lat, dec = math.radians(latitude), math.radians(decl)
    return math.degrees(math.acos(-math.tan(lat) * math.tan(dec)))


def solar_noon(longitude: float) -> float:
    """Local solar noon as minutes since UTC midnight."""
    return 720.0 - 4.0 * longitude


def sunrise(observer: Observer, day: date) -> time:
    """Sunrise in UTC."""
    ha = hour_angle(observer.latitude, declination(day.timetuple().tm_yday))
    minutes = int(solar_noon(observer.longitude) - 4.0 * ha)
    return time(minutes // 60 % 24, minutes % 60)


def sunset(observer: Observer, day: date) -> time:
    """Sunset in UTC."""
    ha = hour_angle(observer.latitude, declination(day.timetuple().tm_yday))
    minutes = int(solar_noon(observer.longitude) + 4.0 * ha)
    return time(minutes // 60 % 24, minutes % 60)
'''

CORE_V2 = '''\
"""Solar position math: declination, hour angle, sunrise and sunset.

Formulas follow the NOAA approximations; accuracy is within a couple
of minutes for latitudes below the polar circles.
"""

import math
from dataclasses import dataclass
from datetime import date, time

from .utils import minutes_to_time


@dataclass(frozen=True)
class Observer:
    """A point on Earth in degrees; east and north are positive."""

    latitude: float
    longitude: float


def declination(day_of_year: int) -> float:
    """Solar declination in degrees for a day of the year."""
    return -23.44 * math.cos(math.radians(360.0 / 365.0 * (day_of_year + 10)))


def hour_angle(latitude: float, decl: float) -> float | None:
    """Half the day arc in degrees; None during polar day or night."""
    lat, dec = math.radians(latitude), math.radians(decl)
    cos_ha = -math.tan(lat) * math.tan(dec)
    if cos_ha < -1.0 or cos_ha > 1.0:
        return None
    return math.degrees(math.acos(cos_ha))


def solar_noon(longitude: float) -> float:
    """Local solar noon as minutes since UTC midnight."""
    return 720.0 - 4.0 * longitude


def sunrise(observer: Observer, day: date) -> time | None:
    """Sunrise in UTC, or None during polar day or night."""
    ha = hour_angle(observer.latitude, declination(day.timetuple().tm_yday))
    if ha is None:
        return None
    raw_minutes = solar_noon(observer.longitude) - 4.0 * ha
    minutes = int(raw_minutes)
    return minutes_to_time(minutes)


def sunset(observer: Observer, day: date) -> time | None:
    """Sunset in UTC, or None during polar day or night."""
    ha = hour_angle(observer.latitude, declination(day.timetuple().tm_yday))
    if ha is None:
        return None
    raw_minutes = solar_noon(observer.longitude) + 4.0 * ha
    minutes = int(raw_minutes)
    return minutes_to_time(minutes)
'''

# Рабочее дерево: word-diff (int → round), вызов функции из utils
# на изменённой строке (цель go-to-definition) и хунк в импортах.
CORE_WT = (
    CORE_V2
    .replace('from .utils import minutes_to_time',
             'from .utils import minutes_to_time, normalize_degrees')
    .replace('return 720.0 - 4.0 * longitude',
             'return 720.0 - 4.0 * normalize_degrees(longitude)')
    .replace('minutes = int(raw_minutes)', 'minutes = round(raw_minutes)')
)

CLI_V1 = '''\
"""Command-line interface: sunrise and sunset for a location."""

import argparse
from datetime import date

from .core import Observer, sunrise, sunset


def main(argv: 'list[str] | None' = None) -> int:
    parser = argparse.ArgumentParser(prog='sundial')
    parser.add_argument('latitude', type=float)
    parser.add_argument('longitude', type=float)
    args = parser.parse_args(argv)
    observer = Observer(args.latitude, args.longitude)
    day = date.today()
    print(sunrise(observer, day), sunset(observer, day))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
'''

CLI_V2 = CLI_V1.replace(
    "    args = parser.parse_args(argv)",
    "    parser.add_argument('--date', help='ISO date, default today')\n"
    "    args = parser.parse_args(argv)",
).replace(
    '    day = date.today()',
    '    day = date.fromisoformat(args.date) if args.date else date.today()',
)

CLI_V3 = CLI_V2.replace(
    '    print(sunrise(observer, day), sunset(observer, day))\n    return 0',
    '''    up, down = sunrise(observer, day), sunset(observer, day)
    if up is None or down is None:
        print(f'{day}  polar day or night at {args.latitude:+.1f}')
        return 1
    print(f'{day}  sunrise {up:%H:%M}  sunset {down:%H:%M}  (UTC)')
    return 0''',
)

APP_WT = CLI_V3.replace(
    "argparse.ArgumentParser(prog='sundial')",
    "argparse.ArgumentParser(prog='sundial', description='Sunrise and sunset times')",
)

TESTS_V1 = '''\
import unittest
from datetime import date

from sundial.core import Observer, declination, sunrise, sunset


class CoreTest(unittest.TestCase):
    def test_declination_solstices(self):
        self.assertAlmostEqual(declination(172), 23.4, delta=0.2)
        self.assertAlmostEqual(declination(355), -23.4, delta=0.2)

    def test_sunset_after_sunrise(self):
        lisbon = Observer(latitude=38.7, longitude=-9.1)
        day = date(2026, 6, 1)
        self.assertLess(sunrise(lisbon, day), sunset(lisbon, day))
'''

TESTS_V2 = TESTS_V1 + '''
    def test_polar_night_has_no_sunrise(self):
        svalbard = Observer(latitude=78.2, longitude=15.6)
        self.assertIsNone(sunrise(svalbard, date(2026, 12, 21)))
'''

TESTS_WT = TESTS_V2 + '''
    def test_sunset_rounding(self):
        # regression: truncation shifted sunset a minute early
        quito = Observer(latitude=-0.2, longitude=-78.5)
        down = sunset(quito, date(2026, 3, 20))
        self.assertIsNotNone(down)
'''

MOON_V1 = '''\
"""Moon phase approximation from the synodic month."""

from datetime import date

SYNODIC_MONTH = 29.530588853
_KNOWN_NEW_MOON = date(2000, 1, 6)

PHASES = (
    'new moon', 'waxing crescent', 'first quarter', 'waxing gibbous',
    'full moon', 'waning gibbous', 'last quarter', 'waning crescent',
)


def moon_age(day: date) -> float:
    """Days since the last new moon."""
    return (day - _KNOWN_NEW_MOON).days % SYNODIC_MONTH


def phase_name(day: date) -> str:
    """Human-readable phase name for a date."""
    index = int(moon_age(day) / SYNODIC_MONTH * 8 + 0.5) % 8
    return PHASES[index]
'''

MOON_V2 = MOON_V1.replace(
    'from datetime import date',
    'import math\nfrom datetime import date',
) + '''

def illumination(day: date) -> float:
    """Illuminated fraction of the lunar disc, 0..1."""
    angle = 2 * math.pi * moon_age(day) / SYNODIC_MONTH
    return (1 - math.cos(angle)) / 2
'''

TESTS_MOON = '''\
import unittest
from datetime import date

from sundial.moon import illumination, moon_age, phase_name


class MoonTest(unittest.TestCase):
    def test_known_new_moon(self):
        self.assertAlmostEqual(moon_age(date(2000, 1, 6)), 0.0)

    def test_full_moon_is_lit(self):
        day = date(2000, 1, 21)
        self.assertEqual(phase_name(day), 'full moon')
        self.assertGreater(illumination(day), 0.95)
'''

TZ_V1 = '''\
"""Timezone-aware wrappers around the UTC core (experimental)."""

from datetime import date, datetime, time, tzinfo

from .core import Observer, sunrise, sunset


def local_sunrise(observer: Observer, day: date, tz: tzinfo) -> 'time | None':
    """Sunrise converted to the given timezone."""
    utc = sunrise(observer, day)
    if utc is None:
        return None
    return datetime.combine(day, utc).astimezone(tz).time()


def local_sunset(observer: Observer, day: date, tz: tzinfo) -> 'time | None':
    """Sunset converted to the given timezone."""
    utc = sunset(observer, day)
    if utc is None:
        return None
    return datetime.combine(day, utc).astimezone(tz).time()
'''

TZ_V2 = TZ_V1 + '''

# TODO: DST transition days — sunrise may land on a skipped hour;
# datetime.astimezone folds it silently, document the behaviour.
'''

NOTIFY_WT = '''\
"""Desktop notification for the upcoming sunset (WIP)."""

import subprocess


def notify_sunset(minutes_left: int) -> None:
    """Fire a macOS notification via osascript."""
    text = f'Sunset in {minutes_left} min'
    subprocess.run(
        ['osascript', '-e', f'display notification "{text}" with title "sundial"'],
        check=False,
    )
'''


# ---------------------------------------------------------------
# Сборка git-репозитория
# ---------------------------------------------------------------

_GIT_ENV = {
    'GIT_AUTHOR_NAME': 'Alex Doe', 'GIT_AUTHOR_EMAIL': 'alex@sundial.dev',
    'GIT_COMMITTER_NAME': 'Alex Doe', 'GIT_COMMITTER_EMAIL': 'alex@sundial.dev',
    'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_SYSTEM': os.devnull,
}


class _Repo:
    """Сборка истории: файлы и коммиты с ровным ходом дат."""

    def __init__(self, path: str, start: datetime):
        self.path = path
        self.clock = start

    def git(self, *args: str) -> None:
        env = {**os.environ, **_GIT_ENV,
               'GIT_AUTHOR_DATE': self.clock.isoformat(),
               'GIT_COMMITTER_DATE': self.clock.isoformat()}
        subprocess.run(['git', '-C', self.path, *args], check=True,
                       capture_output=True, env=env)

    def write(self, rel: str, content: str) -> None:
        p = os.path.join(self.path, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, 'w', encoding='utf-8') as f:
            f.write(content)

    def commit(self, message: str, files: 'dict[str, str]') -> None:
        for rel, content in files.items():
            self.write(rel, content)
        self.clock += timedelta(hours=31)
        self.git('add', '-A')
        self.git('commit', '-q', '-m', message)


def build_repo(repo: str) -> None:
    """Репозиторий sundial: ~18 коммитов, merge, живая ветка и
    незакоммиченные правки всех статусов (staged/modified/renamed/
    untracked).
    """
    os.makedirs(repo)
    r = _Repo(repo, datetime.now().astimezone() - timedelta(days=24))
    r.git('init', '-q', '-b', 'main')

    r.commit('init: project skeleton', {
        'pyproject.toml': PYPROJECT.replace('0.2.0', '0.1.0'),
        'README.md': README_V1,
        'sundial/__init__.py': INIT_V1,
    })
    r.commit('utils: angle helpers', {'sundial/utils.py': UTILS_V1})
    r.commit('core: declination and hour angle', {'sundial/core.py': CORE_V1})
    r.commit('cli: sunrise/sunset for a location', {'sundial/cli.py': CLI_V1})
    r.commit('tests: declination and day ordering', {'tests/test_core.py': TESTS_V1})
    r.commit('core: handle polar day and night', {
        'sundial/utils.py': UTILS_V2,
        'sundial/core.py': CORE_V2,
    })
    r.commit('cli: --date option', {'sundial/cli.py': CLI_V2})
    r.commit('README: usage example', {'README.md': README_V2})
    r.commit('tests: polar night has no sunrise', {'tests/test_core.py': TESTS_V2})
    r.commit('cli: friendlier output and polar guard', {'sundial/cli.py': CLI_V3})

    r.git('checkout', '-q', '-b', 'feature/moon-phase')
    r.commit('moon: phase name from synodic month', {'sundial/moon.py': MOON_V1})
    r.commit('moon: illumination fraction', {'sundial/moon.py': MOON_V2})
    r.commit('tests: moon phases', {'tests/test_moon.py': TESTS_MOON})
    r.git('checkout', '-q', 'main')
    r.clock += timedelta(hours=20)
    r.git('merge', '-q', '--no-ff', '-m', "Merge branch 'feature/moon-phase'",
          'feature/moon-phase')

    r.git('checkout', '-q', '-b', 'feature/timezones')
    r.commit('tz: timezone-aware sunrise and sunset', {'sundial/tz.py': TZ_V1})
    r.commit('tz: note DST transition behaviour', {'sundial/tz.py': TZ_V2})
    r.git('checkout', '-q', 'main')

    r.commit('README: moon phase example', {'README.md': README_V3})
    r.commit('release 0.2.0', {
        'pyproject.toml': PYPROJECT,
        'sundial/__init__.py': INIT_V2,
    })

    # незакоммиченное состояние: staged, renamed, modified, untracked
    r.write('sundial/utils.py', UTILS_STAGED)
    r.git('add', 'sundial/utils.py')
    r.git('mv', 'sundial/cli.py', 'sundial/app.py')
    r.write('sundial/app.py', APP_WT)
    r.write('sundial/core.py', CORE_WT)
    r.write('tests/test_core.py', TESTS_WT)
    r.write('sundial/notify.py', NOTIFY_WT)


# ---------------------------------------------------------------
# Сборка фейкового хранилища Claude Code
# ---------------------------------------------------------------

def _encode(path: str) -> str:
    """Путь проекта → имя папки в projects/ (как в Claude Code)."""
    return path.replace('/', '-').replace('.', '-')


class _Session:
    """Сборка jsonl-сессии: линейная цепочка uuid/parentUuid."""

    def __init__(self, sid: str, cwd: str, start: datetime, branch: str = 'main'):
        self.sid = sid
        self.cwd = cwd
        self.branch = branch
        self.clock = start
        self.prev = None
        self.n = 0
        self.records: list[dict] = []

    def _base(self, type_: str) -> dict:
        self.n += 1
        uid = f'{self.sid}-{self.n:02d}'
        rec = {
            'type': type_, 'uuid': uid, 'parentUuid': self.prev,
            'sessionId': self.sid, 'cwd': self.cwd, 'gitBranch': self.branch,
            'entrypoint': 'cli',
            'timestamp': self.clock.isoformat().replace('+00:00', 'Z'),
        }
        self.prev = uid
        self.clock += timedelta(seconds=40)
        return rec

    def user(self, text: str) -> None:
        rec = self._base('user')
        rec['message'] = {'role': 'user', 'content': text}
        self.records.append(rec)

    def assistant(self, text: str) -> None:
        rec = self._base('assistant')
        rec['message'] = {'role': 'assistant',
                          'content': [{'type': 'text', 'text': text}]}
        self.records.append(rec)

    def tool(self, name: str, tool_input: dict) -> str:
        rec = self._base('assistant')
        tid = f'toolu_{self.n:02d}'
        rec['message'] = {'role': 'assistant', 'content': [
            {'type': 'tool_use', 'id': tid, 'name': name, 'input': tool_input}]}
        self.records.append(rec)
        return tid

    def result(self, tid: str, content: str, *, error: bool = False,
               extra: 'dict | None' = None) -> None:
        rec = self._base('user')
        block = {'type': 'tool_result', 'tool_use_id': tid, 'content': content}
        if error:
            block['is_error'] = True
        rec['message'] = {'role': 'user', 'content': [block]}
        if extra is not None:
            rec['toolUseResult'] = extra
        self.records.append(rec)

    def ai_title(self, title: str) -> None:
        self.records.append(
            {'type': 'ai-title', 'aiTitle': title, 'sessionId': self.sid})

    def custom_title(self, title: str) -> None:
        self.records.append(
            {'type': 'custom-title', 'customTitle': title, 'sessionId': self.sid})

    def dump(self, project_dir: str, mtime: datetime) -> None:
        path = os.path.join(project_dir, self.sid + '.jsonl')
        with open(path, 'w', encoding='utf-8') as f:
            for rec in self.records:
                f.write(json.dumps(rec, ensure_ascii=False) + '\n')
        os.utime(path, (mtime.timestamp(), mtime.timestamp()))


def _rich_session(repo: str, now: datetime) -> _Session:
    """Богатая сессия для превью: промпт, свёрнутая разведка, красный
    вывод упавшего теста, diff правок и markdown с код-блоком.
    """
    s = _Session('4b6de0a1-9c2f-4f7e-8d31-000000000001', repo,
                 now - timedelta(minutes=42))
    s.user('Sunset time is off by a minute near the equator — '
           'find and fix the rounding bug')
    s.ai_title('Fix sunset rounding bug')
    s.assistant('The symptom smells like truncation. Plan:\n\n'
                '- find where minutes are converted to `time`\n'
                '- add a regression test first\n'
                '- fix and re-run the suite')
    tid = s.tool('Grep', {'pattern': 'int(raw_minutes)', 'path': 'sundial'})
    s.result(tid, 'sundial/core.py:48:    minutes = int(raw_minutes)\n'
                  'sundial/core.py:58:    minutes = int(raw_minutes)')
    tid = s.tool('Read', {'file_path': os.path.join(repo, 'sundial/core.py')})
    core_lines = CORE_V2.splitlines()
    s.result(tid, '\n'.join(core_lines[:40]),
             extra={'file': {'numLines': len(core_lines)}})
    s.assistant('Found it — `int()` drops up to 59 seconds, so any sunset at '
                '`…:41.9` is reported as `…:41`. Adding a regression test first.')
    tid = s.tool('Edit', {
        'file_path': os.path.join(repo, 'tests/test_core.py'),
        'old_string': 'self.assertIsNone(sunrise(svalbard, date(2026, 12, 21)))',
        'new_string': 'self.assertIsNone(sunrise(svalbard, date(2026, 12, 21)))\n\n'
                      '    def test_sunset_rounding(self): ...',
    })
    s.result(tid, 'ok', extra={'structuredPatch': [{
        'oldStart': 18, 'newStart': 18, 'lines': [
            '         svalbard = Observer(latitude=78.2, longitude=15.6)',
            '         self.assertIsNone(sunrise(svalbard, date(2026, 12, 21)))',
            '+',
            '+    def test_sunset_rounding(self):',
            '+        quito = Observer(latitude=-0.2, longitude=-78.5)',
            '+        down = sunset(quito, date(2026, 3, 20))',
            '+        self.assertEqual(down.minute, 42)',
        ]}]})
    tid = s.tool('Bash', {'command': 'python3 -m unittest discover -s tests'})
    rule = '-' * 70
    s.result(tid, 'F...\n'
                  f'{"=" * 70}\n'
                  'FAIL: test_sunset_rounding (tests.test_core.CoreTest)\n'
                  f'{rule}\n'
                  'Traceback (most recent call last):\n'
                  f'  File "{repo}/tests/test_core.py", line 24\n'
                  '    self.assertEqual(down.minute, 42)\n'
                  'AssertionError: 41 != 42\n'
                  f'{rule}\n'
                  'Ran 4 tests in 0.004s\n\n'
                  'FAILED (failures=1)', error=True)
    tid = s.tool('Edit', {
        'file_path': os.path.join(repo, 'sundial/core.py'),
        'old_string': 'minutes = int(raw_minutes)',
        'new_string': 'minutes = round(raw_minutes)',
    })
    s.result(tid, 'ok', extra={'structuredPatch': [
        {'oldStart': 46, 'newStart': 46, 'lines': [
            '     raw_minutes = solar_noon(observer.longitude) - 4.0 * ha',
            '-    minutes = int(raw_minutes)',
            '+    minutes = round(raw_minutes)',
            '     return minutes_to_time(minutes)',
        ]},
        {'oldStart': 56, 'newStart': 56, 'lines': [
            '     raw_minutes = solar_noon(observer.longitude) + 4.0 * ha',
            '-    minutes = int(raw_minutes)',
            '+    minutes = round(raw_minutes)',
            '     return minutes_to_time(minutes)',
        ]}]})
    tid = s.tool('Bash', {'command': 'python3 -m unittest discover -s tests'})
    s.result(tid, '....\n' + '-' * 70 + '\nRan 4 tests in 0.003s\n\nOK')
    s.assistant('Fixed — the bug was plain truncation:\n\n'
                '```python\n'
                'minutes = round(raw_minutes)  # was: int(raw_minutes)\n'
                '```\n\n'
                'All 4 tests pass now, including the new regression test.')
    s.user('great — update the changelog too')
    return s


def _dialogue(sid: str, cwd: str, start: datetime, prompt: str,
              answer: str, branch: str = 'main') -> _Session:
    """Короткая сессия «вопрос → ответ»."""
    s = _Session(sid, cwd, start, branch)
    s.user(prompt)
    s.assistant(answer)
    return s


def _registry(sessions_dir: str, name: str, sid: str, cwd: str, agent: str,
              status: str, now: datetime, kind: str = 'interactive') -> None:
    """Запись реестра живых процессов. pid=1 (launchd) всегда жив,
    а PermissionError на kill(pid, 0) кит трактует как «процесс есть».
    """
    rec = {
        'pid': 1, 'sessionId': sid, 'cwd': cwd, 'kind': kind,
        'entrypoint': 'cli', 'name': agent, 'status': status,
        'startedAt': int((now - timedelta(minutes=50)).timestamp() * 1000),
        'updatedAt': int(now.timestamp() * 1000),
    }
    with open(os.path.join(sessions_dir, name + '.json'), 'w',
              encoding='utf-8') as f:
        json.dump(rec, f)


def build_claude(claude: str, repo: str) -> None:
    """Хранилище Claude Code: 3 проекта, живые busy/waiting-сессии,
    фоновый агент, custom-заголовок и богатое превью.
    """
    projects = os.path.join(claude, 'projects')
    sessions_dir = os.path.join(claude, 'sessions')
    os.makedirs(sessions_dir)
    now = datetime.now(timezone.utc)

    main_dir = os.path.join(projects, _encode(repo))
    os.makedirs(main_dir)

    rich = _rich_session(repo, now)
    rich.dump(main_dir, now - timedelta(minutes=4))

    waiting = _dialogue(
        '4b6de0a1-9c2f-4f7e-8d31-000000000002', repo,
        now - timedelta(minutes=35),
        'How does hour_angle handle polar night? Explain briefly',
        'When `|cos_ha| > 1` the sun never crosses the horizon that day:\n\n'
        '- **polar night** — `cos_ha > 1`, no sunrise\n'
        '- **polar day** — `cos_ha < -1`, no sunset\n\n'
        'Both return `None`, and the CLI prints a friendly message instead.')
    waiting.dump(main_dir, now - timedelta(minutes=25))

    bg = _dialogue(
        '4b6de0a1-9c2f-4f7e-8d31-000000000003', repo,
        now - timedelta(minutes=15),
        'Profile the sunrise loop over a year of dates and cache '
        'declination if it helps',
        'Starting with a quick benchmark of `declination()` calls.',
        branch='feature/timezones')
    bg.dump(main_dir, now - timedelta(minutes=2))

    renamed = _dialogue(
        '4b6de0a1-9c2f-4f7e-8d31-000000000004', repo,
        now - timedelta(days=1, hours=3),
        'What is left before we can tag 0.2.0?',
        'Remaining items:\n\n'
        '1. changelog entry\n'
        '2. bump `__version__`\n'
        '3. tag and push')
    renamed.custom_title('release checklist')
    renamed.dump(main_dir, now - timedelta(days=1))

    aurora = os.path.expanduser('~/Projects/aurora')
    aurora_dir = os.path.join(projects, _encode(aurora))
    os.makedirs(aurora_dir)
    _dialogue('7c2f81d4-3e5a-4b90-a1c2-000000000005', aurora,
              now - timedelta(days=3, hours=2),
              'Add a dark mode toggle to the settings screen',
              'Done — the toggle persists via `localStorage` and follows '
              'the system theme by default.'
              ).dump(aurora_dir, now - timedelta(days=3))
    _dialogue('7c2f81d4-3e5a-4b90-a1c2-000000000006', aurora,
              now - timedelta(days=4, hours=1),
              'Fix the flaky websocket reconnect test',
              'The test raced the reconnect timer; replaced the sleep with '
              'an event wait.'
              ).dump(aurora_dir, now - timedelta(days=4))

    vega = os.path.expanduser('~/Projects/vega')
    vega_dir = os.path.join(projects, _encode(vega))
    os.makedirs(vega_dir)
    _dialogue('9e4a72b8-6d1c-4c33-b7f0-000000000007', vega,
              now - timedelta(days=8, hours=5),
              'Migrate CI from Travis to GitHub Actions',
              'Migrated — the matrix covers three Python versions and the '
              'badge in the README points at the new workflow.'
              ).dump(vega_dir, now - timedelta(days=8))

    _registry(sessions_dir, 'demo-busy', rich.sid, repo, 'amber-fox',
              'busy', now)
    _registry(sessions_dir, 'demo-waiting', waiting.sid, repo, 'cyan-otter',
              'waiting', now)
    _registry(sessions_dir, 'demo-bg', bg.sid, repo, 'jade-heron',
              'busy', now, kind='bg')


# ---------------------------------------------------------------
# CLI
# ---------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description='Build the familiar demo stand for screenshots.')
    parser.add_argument('--dir', default='/tmp/familiar-demo',
                        help='stand directory (default: /tmp/familiar-demo)')
    parser.add_argument('--wipe', action='store_true',
                        help='recreate the directory if it is not empty')
    args = parser.parse_args()

    root = os.path.realpath(args.dir)
    if os.path.isdir(root) and os.listdir(root):
        if not args.wipe:
            print(f'{args.dir} is not empty — pass --wipe to recreate',
                  file=sys.stderr)
            return 1
        shutil.rmtree(root)
    os.makedirs(root, exist_ok=True)

    repo = os.path.join(root, 'repo')
    claude = os.path.join(root, 'claude')
    build_repo(repo)
    build_claude(claude, repo)

    print(f'''Demo stand is ready:
  review/log repo : {repo}
  Claude storage  : {claude}

Launch a dedicated kitty instance with the fake storage:

  CLAUDE_CONFIG_DIR={claude} \\
    /Applications/kitty.app/Contents/MacOS/kitty --detach --directory {repo}

Inside it press:
  Cmd+Shift+S  session (projects / sessions / rich preview)
  Cmd+Shift+R  review  (staged / modified / renamed / untracked)
  Cmd+Shift+L  log     (history; `a` — all branches, `i` — details)''')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
