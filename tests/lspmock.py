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


class FakeCall:
    """Call с ответом, который тест задаёт сам: сразу или позже."""

    def __init__(self, result=None, error=None):
        self._result = result
        self.error = error
        self.cancelled = 0

    def result(self, timeout):
        if self.error:
            raise self.error
        return self._result

    def cancel(self):
        self.cancelled += 1


class FakeSession:
    def __init__(self, defs=None, syms=None, error=None, status=None, completion=None,
                 resolve=None, signature=None, triggers=('.',), resolve_provider=False,
                 signature_triggers=('(', ','), ready=True, has_completion=True):
        self.spec = SimpleNamespace(lang='python', timeout=5.0)
        self.encoding = 'utf-16'
        self.defs = defs or {}
        self.syms = syms or {}
        self.error = error
        self._status = status or Progress('ready', -1, '', 0.0, 0)
        self.opened = []
        self.asked = []
        # автодополнение: ответ — dict/list как у сервера либо
        # функция (params) → ответ, если он зависит от позиции
        self.completion = completion
        self.resolve = resolve or {}
        self.signature = signature
        self.completion_triggers = tuple(triggers)
        self.resolve_provider = resolve_provider
        self.signature_triggers = tuple(signature_triggers)
        self.signature_retriggers = ()
        self.has_completion = has_completion
        self._ready = ready
        self.completions = []
        self.resolved = []
        self.signatures = []

    def wait_ready(self, timeout):
        return True

    def ready(self):
        return self._ready

    def alive(self):
        return True

    def completion_call(self, path, line, character, context):
        params = (os.path.basename(path), line, character, context)
        self.completions.append(params)
        result = self.completion(params) if callable(self.completion) else self.completion
        return FakeCall(result)

    def resolve_call(self, item):
        self.resolved.append(item.get('label'))
        return FakeCall({**item, **self.resolve.get(item.get('label'), {})})

    def signature_call(self, path, line, character, context):
        self.signatures.append((line, character, context))
        return FakeCall(self.signature)

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

    def language(self, rel, first_line=''):
        return None if self.raises else 'python'

    def peek(self, rel, first_line=''):
        return None if self.raises else self.session

    def active(self):
        return [self.session]

    def stop_all(self):
        self.stopped = True
