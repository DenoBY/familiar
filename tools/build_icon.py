#!/usr/bin/env python3
"""Сборка иконки приложения из config/icon/familiar.svg.

Делает два файла:
- config/icon/kitty.app.icns — то, что familiar кладёт в каталог
  конфига kitty; рисунок вписан в поля macOS (см. CONTENT_RATIO),
  иначе в Dock иконка выглядит крупнее соседних;
- config/icon/familiar.png — логотип для README, без полей.

Нужны rsvg-convert (brew install librsvg) и iconutil (в macOS).
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile


# Apple рисует squircle в 824 px из холста 1024 — остальное
# прозрачные поля. Иконка без них рядом с системными выглядит
# больше, хотя формально того же размера.
CONTENT_RATIO = 824 / 1024

# (сторона в px, имя кадра) — набор, который ждёт iconutil.
SIZES = (
    (16, "icon_16x16"), (32, "icon_16x16@2x"),
    (32, "icon_32x32"), (64, "icon_32x32@2x"),
    (128, "icon_128x128"), (256, "icon_128x128@2x"),
    (256, "icon_256x256"), (512, "icon_256x256@2x"),
    (512, "icon_512x512"), (1024, "icon_512x512@2x"),
)

LOGO_SIZE = 512


def repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def padded_svg(source: str) -> str:
    """Тот же рисунок, ужатый до CONTENT_RATIO и центрированный.

    Содержимое исходника уезжает в <g transform>, поэтому арт
    править не нужно — поля живут только в сборке.
    """
    with open(source, encoding="utf-8") as f:
        text = f.read()
    head = text[text.index("<svg"):text.index(">", text.index("<svg")) + 1]
    body = text[text.index(">", text.index("<svg")) + 1:text.rindex("</svg>")]
    # <metadata> исходника ссылается на namespace, объявленный в его
    # корневом теге; в растр он всё равно не попадает, а без него
    # rsvg спотыкается о неизвестный префикс.
    start = body.find("<metadata>")
    if start >= 0:
        body = body[:start] + body[body.index("</metadata>") + len("</metadata>"):]

    view_box = re.search(r'viewBox="([^"]+)"', head)
    if view_box is None:
        raise SystemExit(f"no viewBox in {source}")
    side = float(view_box.group(1).split()[2])
    offset = side * (1 - CONTENT_RATIO) / 2
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{side:g}" height="{side:g}" viewBox="0 0 {side:g} {side:g}">'
        f'<g transform="translate({offset:g},{offset:g}) '
        f'scale({CONTENT_RATIO:.6f})">{body}</g></svg>'
    )


def render(source: str, target: str, size: int) -> None:
    subprocess.run(["rsvg-convert", "-w", str(size), "-h", str(size),
                    source, "-o", target], check=True)


def main() -> int:
    for tool in ("rsvg-convert", "iconutil"):
        if shutil.which(tool) is None:
            print(f"{tool} not found "
                  "(rsvg-convert comes from `brew install librsvg`)",
                  file=sys.stderr)
            return 1

    icon_dir = os.path.join(repo_root(), "config", "icon")
    source = os.path.join(icon_dir, "familiar.svg")

    with tempfile.TemporaryDirectory() as tmp:
        padded = os.path.join(tmp, "padded.svg")
        with open(padded, "w", encoding="utf-8") as f:
            f.write(padded_svg(source))

        iconset = os.path.join(tmp, "familiar.iconset")
        os.mkdir(iconset)
        for size, name in SIZES:
            render(padded, os.path.join(iconset, f"{name}.png"), size)

        icns = os.path.join(icon_dir, "kitty.app.icns")
        subprocess.run(["iconutil", "-c", "icns", iconset, "-o", icns],
                       check=True)

    logo = os.path.join(icon_dir, "familiar.png")
    render(source, logo, LOGO_SIZE)
    print(f"built: {icns}\n       {logo}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
