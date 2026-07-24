"""Утилиты session-кита: возраст сессии и имена особых вызовов.

Без состояния и без обращения к диску. Текстовые примитивы и
раскладку потребители берут напрямую из modules.text /
modules.keylayout.
"""


# Вопрос пользователю: у отклонённого (Esc) вызова своё имя — общий
# заголовок «User answered …» о нём соврал бы.
ASK_TOOL = 'AskUserQuestion'
ASK_REJECTED = 'AskUserQuestionRejected'


def human_age(seconds: float) -> str:
    m = seconds / 60
    if m < 1:
        return 'just now'
    if m < 60:
        return f'{int(m)}m ago'
    h = m / 60
    if h < 24:
        return f'{int(h)}h ago'
    d = h / 24
    if d < 30:
        return f'{int(d)}d ago'
    return f'{int(d / 30)}mo ago'
