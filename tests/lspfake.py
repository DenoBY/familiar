"""Фейковый language server для тестов: говорит по LSP, ведёт себя по
сценарию.

Фрейминг здесь свой, а не из modules.lsp.rpc: тест должен проверять
совместимость двух независимых реализаций, иначе общая ошибка в
разборе заголовков осталась бы незамеченной.

Запуск: python3 lspfake.py <scenario.json>
Ключи сценария:
    capabilities   что отдать на initialize
    definitions    "<файл>:<строка>:<колонка>" → список Location
    symbols        имя → список SymbolInformation
    progress       список [kind, message, percentage[, token]] после
                   initialized; токен по умолчанию один на всех
    ready          слать ли кастомную нотификацию о конце индексации
    ready_after    через сколько секунд её слать
    die_on         метод, на котором оборвать соединение
    hang_on        метод, на который не отвечать
    delay          пауза перед каждым ответом
    want_config    спросить ли workspace/configuration после initialized
"""

import json
import os
import sys
import threading
import time


_ZERO = {'start': {'line': 0, 'character': 0}, 'end': {'line': 0, 'character': 0}}


def read_frame(stream):
    length = None
    while True:
        line = stream.readline()
        if not line:
            return None
        line = line.strip()
        if not line:
            break
        name, _, value = line.partition(b':')
        if name.strip().lower() == b'content-length':
            length = int(value)
    if length is None:
        return None
    body = b''
    while len(body) < length:
        chunk = stream.read(length - len(body))
        if not chunk:
            return None
        body += chunk
    return json.loads(body)


class FakeServer:
    def __init__(self, scenario: dict) -> None:
        self.s = scenario
        self.out = sys.stdout.buffer
        self.lock = threading.Lock()
        self.opened: 'dict[str, str]' = {}
        self.running = True

    def send(self, payload: dict) -> None:
        body = json.dumps(payload).encode('utf-8')
        with self.lock:
            self.out.write(b'Content-Length: %d\r\n\r\n' % len(body) + body)
            self.out.flush()

    def reply(self, mid, result) -> None:
        self.send({'jsonrpc': '2.0', 'id': mid, 'result': result})

    def notify(self, method: str, params=None) -> None:
        self.send({'jsonrpc': '2.0', 'method': method, 'params': params or {}})

    def serve(self) -> None:
        while self.running:
            msg = read_frame(sys.stdin.buffer)
            if msg is None:
                return
            method = msg.get('method') or ''
            if method == self.s.get('die_on'):
                os._exit(1)
            if method == self.s.get('hang_on'):
                continue
            if self.s.get('delay'):
                time.sleep(float(self.s['delay']))
            self.handle(msg, method)

    def handle(self, msg: dict, method: str) -> None:
        mid = msg.get('id')
        params = msg.get('params') or {}
        if method == 'initialize':
            self.reply(mid, {'capabilities': self.s.get('capabilities', {
                'definitionProvider': True, 'workspaceSymbolProvider': True,
                'positionEncoding': 'utf-16'})})
        elif method == 'initialized':
            threading.Thread(target=self.after_init, daemon=True).start()
        elif method == 'textDocument/didOpen':
            doc = params.get('textDocument') or {}
            self.opened[doc.get('uri', '')] = doc.get('text', '')
        elif method == 'textDocument/didChange':
            uri = (params.get('textDocument') or {}).get('uri', '')
            changes = params.get('contentChanges') or [{}]
            self.opened[uri] = changes[-1].get('text', '')
        elif method == 'textDocument/didClose':
            self.opened.pop((params.get('textDocument') or {}).get('uri', ''), None)
        elif method == 'textDocument/definition':
            self.reply(mid, self.definition(params))
        elif method == 'workspace/symbol':
            query = params.get('query', '')
            if query == '__path__':
                # чем тест проверяет, какой PATH достался серверу
                self.reply(mid, [{'name': os.environ.get('PATH', ''), 'kind': 12,
                                  'location': {'uri': '', 'range': _ZERO}}])
            elif query == '__opened__':
                # чем тест проверяет, какой текст сервер реально видит
                self.reply(mid, [{'name': text, 'kind': 12,
                                  'location': {'uri': uri, 'range': _ZERO}}
                                 for uri, text in sorted(self.opened.items())])
            else:
                self.reply(mid, self.s.get('symbols', {}).get(query, []))
        elif method == 'shutdown':
            self.reply(mid, None)
        elif method == 'exit':
            self.running = False
        elif mid is not None:
            self.reply(mid, None)

    def definition(self, params: dict):
        uri = (params.get('textDocument') or {}).get('uri', '')
        pos = params.get('position') or {}
        key = f"{os.path.basename(uri)}:{pos.get('line')}:{pos.get('character')}"
        return self.s.get('definitions', {}).get(key, [])

    def after_init(self) -> None:
        if self.s.get('want_config'):
            self.send({'jsonrpc': '2.0', 'id': 9001,
                       'method': 'workspace/configuration',
                       'params': {'items': [{'section': 'test'}]}})
        for row in self.s.get('progress', []):
            kind, message, pct = row[0], row[1], row[2]
            value = {'kind': kind}
            if message:
                value['message'] = message
            if pct is not None:
                value['percentage'] = pct
            token = row[3] if len(row) > 3 else 'idx'
            self.notify('$/progress', {'token': token, 'value': value})
        if self.s.get('ready'):
            time.sleep(float(self.s.get('ready_after', 0)))
            self.notify(self.s['ready'])


def main() -> None:
    scenario = {}
    if len(sys.argv) > 1:
        with open(sys.argv[1], encoding='utf-8') as f:
            scenario = json.load(f)
    FakeServer(scenario).serve()


if __name__ == '__main__':
    main()
