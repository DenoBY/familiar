"""Маркировка overlay-окна плагина для взаимного вытеснения (см. kittens.conf)."""

import base64
import sys

OVERLAY_VAR = 'cc_plugin'


def mark_overlay(name: str) -> None:
    """Пометить своё окно kitty user-var'ом cc_plugin=<name> (OSC 1337 SetUserVar).

    Значение кодируется base64 (требование протокола). Вызывать в начале main()
    до старта TUI-Loop. Метка исчезает вместе с окном kitten — чистить не нужно.
    """
    val = base64.b64encode(name.encode()).decode('ascii')
    sys.stdout.write(f'\033]1337;SetUserVar={OVERLAY_VAR}={val}\007')
    sys.stdout.flush()
