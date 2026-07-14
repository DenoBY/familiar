"""Ненавязчивая проверка выхода новой версии familiar.

brew сам не сообщает об обновлениях, поэтому киты раз в сутки
опрашивают GitHub Releases фоновым потоком и кэшируют ответ в
~/.cache/familiar; подсказка «brew upgrade» показывается из кэша
при следующем открытии кита и не чаще раза в сутки. Сеть не
трогается в колбэках Loop и не роняет кит: любая ошибка — просто
отсутствие подсказки. FAMILIAR_UPDATE_CHECK=0 выключает и запросы,
и подсказку.
"""

import json
import os
import threading
import time
import urllib.request


TAGS_URL = 'https://api.github.com/repos/DenoBY/familiar/tags?per_page=100'
UPDATE_ENV = 'FAMILIAR_UPDATE_CHECK'
INTERVAL = 24 * 60 * 60

# От __file__, как в theme.py: у модулей пакета он есть в обоих
# процессах, а раскладка brew (libexec/…) повторяет репозиторий.
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def installed_version() -> 'str | None':
    try:
        with open(os.path.join(ROOT, 'VERSION'), encoding='utf-8') as f:
            return f.read().strip() or None
    except OSError:
        return None


def parse_version(s: str) -> 'tuple[int, ...] | None':
    try:
        return tuple(int(p) for p in s.lstrip('v').split('.'))
    except ValueError:
        return None


def _enabled() -> bool:
    return os.environ.get(UPDATE_ENV, '') != '0'


def _cache_path() -> str:
    base = os.environ.get('XDG_CACHE_HOME') or os.path.expanduser('~/.cache')
    return os.path.join(base, 'familiar', 'update.json')


def _load() -> dict:
    try:
        with open(_cache_path(), encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(data: dict) -> None:
    path = _cache_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f)
    except OSError:
        pass   # кэш — best effort: без него просто не будет подсказки


def _ts(data: dict, key: str) -> float:
    v = data.get(key, 0)
    return v if isinstance(v, (int, float)) else 0


def _fetch_latest() -> 'str | None':
    # Релизы familiar — теги без GitHub Release (releases/latest
    # отдаёт 404), поэтому смотрим /tags; порядок ответа не
    # гарантирован — берём максимум по semver.
    try:
        with urllib.request.urlopen(TAGS_URL, timeout=5) as resp:
            tags = json.load(resp)
    except (OSError, ValueError):
        return None
    if not isinstance(tags, list):
        return None
    versions = [(v, t['name'].lstrip('v')) for t in tags
                if isinstance(t, dict) and isinstance(t.get('name'), str)
                and (v := parse_version(t['name']))]
    return max(versions)[1] if versions else None


def _refresh() -> None:
    data = _load()
    # checked пишется и при ошибке сети: офлайн-день не должен
    # превращаться в запрос при каждом открытии кита.
    data['checked'] = time.time()
    latest = _fetch_latest()
    if latest:
        data['latest'] = latest
    _save(data)


def start_check() -> None:
    """Запустить суточную фоновую проверку (из main кита)."""
    if not _enabled():
        return
    if time.time() - _ts(_load(), 'checked') < INTERVAL:
        return
    threading.Thread(target=_refresh, daemon=True).start()


def update_hint() -> 'str | None':
    """Подсказка об обновлении — не чаще раза в сутки, иначе None."""
    if not _enabled():
        return None
    cur = parse_version(installed_version() or '')
    data = _load()
    latest = data.get('latest')
    new = parse_version(latest) if isinstance(latest, str) else None
    if not cur or not new or new <= cur:
        return None
    if time.time() - _ts(data, 'notified') < INTERVAL:
        return None
    data['notified'] = time.time()
    _save(data)
    return f'familiar {latest} is out — brew upgrade familiar'
