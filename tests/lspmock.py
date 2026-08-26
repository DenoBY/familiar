"""Фейковые сессия и пул LSP для тестов кита.

Кит проверяется на своём поведении — переход, пикер, футер, — а не на
том, что отвечает настоящий сервер: тому есть test_lsp_session.
"""

import os
from types import SimpleNamespace

import kittymock  # noqa: F401  (регистрирует путь к модулям кита)
from modules.lsp.session import Progress


def loc(path: str, line0: int) -> dict:
    """Location, как его шлёт сервер: строка с нуля."""
    return {'uri': f'file://{path}',
            'range': {'start': {'line': line0, 'character': 0}}}


def sym(name: str, path: str, line0: int, kind: int = 12) -> dict:
    return {'name': name, 'kind': kind, 'location': loc(path, line0)}


class FakeSession:
    def __init__(self, defs=None, syms=None, error=None, status=None):
        self.spec = SimpleNamespace(lang='python')
        self.encoding = 'utf-16'
        self.defs = defs or {}
        self.syms = syms or {}
        self.error = error
        self._status = status or Progress('ready', -1, '', 0.0, 0)
        self.opened = []
        self.asked = []

    def wait_ready(self, timeout):
        return True

    def status(self):
        return self._status

    def open_doc(self, path, text):
        self.opened.append((os.path.basename(path), text))

    def definition(self, path, line, character):
        if self.error:
            raise self.error
        self.asked.append((os.path.basename(path), line, character))
        return self.defs.get((os.path.basename(path), line), [])

    def symbols(self, query):
        if self.error:
            raise self.error
        return self.syms.get(query, [])


class FakePool:
    def __init__(self, session=None, raises=None):
        self.session = session or FakeSession()
        self.raises = raises
        self.stopped = False
        self.asked_for = None

    def session_for(self, rel, first_line=''):
        if self.raises:
            raise self.raises
        self.asked_for = (rel, first_line)
        return self.session

    def active(self):
        return [self.session]

    def stop_all(self):
        self.stopped = True
