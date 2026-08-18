"""Что закрывать, когда закрытие панели подтвердили."""

from ..session.data import running_sessions, session_id_for_pid


def claude_session(window) -> 'dict | None':
    running = running_sessions()
    if not running:
        return None
    try:
        pid = window.child.pid
    except (AttributeError, OSError):
        return None
    if not pid:
        return None
    sid = session_id_for_pid(pid, running)
    return running.get(sid) if sid else None


def close(boss, window) -> None:
    """Закрыть панель — вместе с окном, оставшимся под сессией claude.

    Кит session открывает сессию оверлеем над окном, из которого её
    позвали (overlay-main), поэтому закрытие одного оверлея оставляло
    на месте сессии пустой шелл: ни сплит, ни таб не уходили.
    """
    if claude_session(window) is not None:
        parent = window.overlay_parent
        while parent is not None:
            boss.mark_window_for_close(parent)
            parent = parent.overlay_parent
    boss.mark_window_for_close(window)
