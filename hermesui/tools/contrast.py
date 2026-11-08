#!/usr/bin/env python3
"""`contrast` — проверка контраста по WCAG 2.1 для наших палитр (этап 6).

Зачем инструмент, а не глаз: «выглядит читаемо» не проверяется повторно. Здесь считаются
коэффициенты для пар «текст на фоне» из скина (`modules/theme-hermesui/theme.yaml`) и из
цветов карты проектов (`desktop-plugins/project-map/plugin.js`), с порогами WCAG:

* `4.5:1` — обычный текст (AA);
* `3.0:1` — крупный текст (≥18.66px bold или ≥24px) и элементы интерфейса (границы контролов);
* `1.0` — невидимый, значит провал.

    python tools/contrast.py                 # отчёт по всем парам
    python tools/contrast.py --json          # машинный вывод
    python tools/contrast.py --fail-under    # код выхода 1, если есть провалы

Пары берутся смысловые: то, что реально лежит друг на друге (текст статуса на подложке
карточки, подписи на панели, подсказки на фоне), а не все комбинации подряд.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Optional

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
THEME = REPO / "modules" / "theme-hermesui" / "theme.yaml"
PLUGIN = REPO / "desktop-plugins" / "project-map" / "plugin.js"

BODY, LARGE, UI = 4.5, 3.0, 3.0

# Поверхности оболочки приложения, к которым привязаны карточки. Значения — из темы десктопа:
# светлая по умолчанию; тёмная из `:root.dark` (`--theme-neutral-card #161618`, фон #0d0d0e).
SURFACES = {
    "light": {"panel": "#ffffff", "bg": "#f2f3f5", "text": "#12161c", "muted": "#5b6470"},
    "dark": {"panel": "#161618", "bg": "#0d0d0e", "text": "#ececec", "muted": "#a3a3a3"},
}


def _rgb(color: str) -> Optional[tuple[float, float, float]]:
    value = str(color or "").strip().lower()
    if value.startswith("#"):
        hex_part = value[1:]
        if len(hex_part) == 3:
            hex_part = "".join(ch * 2 for ch in hex_part)
        if len(hex_part) != 6:
            return None
        try:
            return tuple(int(hex_part[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]
        except ValueError:
            return None
    match = re.match(r"rgba?\(([^)]+)\)", value)
    if match:
        parts = [p.strip() for p in match.group(1).split(",")]
        if len(parts) < 3:
            return None
        try:
            return tuple(float(p) / 255 if not p.endswith("%") else float(p.rstrip("%")) / 100
                         for p in parts[:3])  # type: ignore[return-value]
        except ValueError:
            return None
    return None


def _linear(channel: float) -> float:
    return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4


def luminance(color: str) -> Optional[float]:
    rgb = _rgb(color)
    if rgb is None:
        return None
    r, g, b = (_linear(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def alpha(color: str) -> float:
    match = re.match(r"rgba\(([^)]+)\)", str(color or "").strip().lower())
    if not match:
        return 1.0
    parts = [p.strip() for p in match.group(1).split(",")]
    if len(parts) < 4:
        return 1.0
    try:
        return float(parts[3])
    except ValueError:
        return 1.0


def flatten(fg: str, bg: str) -> str:
    """Полупрозрачный цвет на фоне — в непрозрачный: иначе контраст считается не к тому."""
    fg_rgb = _rgb(fg)
    bg_rgb = _rgb(bg)
    if fg_rgb is None or bg_rgb is None:
        return fg
    a = alpha(fg)
    mixed = [fg_rgb[i] * a + bg_rgb[i] * (1 - a) for i in range(3)]
    return "#" + "".join(f"{round(c * 255):02x}" for c in mixed)


def ratio(fg: str, bg: str) -> Optional[float]:
    foreground = flatten(fg, bg)
    lf, lb = luminance(foreground), luminance(bg)
    if lf is None or lb is None:
        return None
    lighter, darker = max(lf, lb), min(lf, lb)
    return round((lighter + 0.05) / (darker + 0.05), 2)


# ── палитры ─────────────────────────────────────────────────────────────────

def parse_theme(path: Path) -> dict[str, dict[str, str]]:
    """Читает `colors:` и `light_colors:` из theme.yaml (плоский YAML без зависимостей)."""
    out: dict[str, dict[str, str]] = {"dark": {}, "light": {}}
    if not path.is_file():
        return out
    section = ""
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        if not line.startswith(" "):
            section = line.split(":", 1)[0].strip()
            continue
        if section not in ("colors", "light_colors"):
            continue
        match = re.match(r"\s+([A-Za-z0-9_]+):\s*\"?([^\"#]*#?[0-9A-Fa-f]{3,8})\"?\s*$", line)
        if match:
            key = "dark" if section == "colors" else "light"
            out[key][match.group(1)] = match.group(2).strip()
    return out


def parse_css_vars(text: str, selector: str) -> dict[str, str]:
    """Достаёт `--pm-*: …` из блока с данным селектором (без вложенности — нам хватает)."""
    block = re.search(re.escape(selector) + r"\s*\{(.*?)\}", text, re.DOTALL)
    if not block:
        return {}
    return {name: value.strip()
            for name, value in re.findall(r"(--pm-[a-z0-9-]+):\s*([^;]+);", block.group(1))}


def parse_map_palettes(path: Path) -> dict[str, dict[str, str]]:
    """Светлый и тёмный наборы `--pm-*` из `STATUS_CSS`."""
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    css = re.search(r"const STATUS_CSS = `(.*?)`\n", text, re.DOTALL)
    body = css.group(1) if css else ""
    return {"light": parse_css_vars(body, ".pm-root"),
            "dark": parse_css_vars(body, ":root.dark .pm-root")}


def _map_pairs(vars_: dict[str, str], mode: str) -> list[tuple[str, str, str, float]]:
    surface = SURFACES[mode]
    pairs: list[tuple[str, str, str, float]] = [
        (f"карта {mode}: текст на панели", surface["text"], surface["panel"], BODY),
        (f"карта {mode}: muted на панели", surface["muted"], surface["panel"], BODY),
        (f"карта {mode}: текст на фоне", surface["text"], surface["bg"], BODY),
        (f"карта {mode}: граница панели на фоне", surface["muted"], surface["bg"], 0.0),
    ]
    for name in ("working", "open", "done", "idle", "empty", "temp"):
        ink = vars_.get(f"--pm-{name}-ink", "")
        tint = vars_.get(f"--pm-{name}-tint", "")
        line = vars_.get(f"--pm-{name}-line", "")
        if ink and tint:
            pairs.append((f"карта {mode}: «{name}» — текст на подложке", ink, tint, BODY))
        if ink:
            pairs.append((f"карта {mode}: «{name}» — текст на панели", ink, surface["panel"], BODY))
        if line:
            pairs.append((f"карта {mode}: «{name}» — граница на панели", line, surface["panel"], 0.0))
    return pairs


def _theme_pairs(colors: dict[str, str], mode: str) -> list[tuple[str, str, str, float]]:
    """Смысловые пары «текст на фоне» для скина: (пара, текст, фон, порог)."""
    pairs: list[tuple[str, str, str, float]] = []
    bg_status = colors.get("status_bar_bg", "#000000")
    bg_menu = colors.get("completion_menu_bg", bg_status)
    pairs += [
        (f"{mode}: текст баннера на статус-баре", colors.get("banner_text", ""), bg_status, BODY),
        (f"{mode}: акцент баннера на статус-баре", colors.get("banner_title", ""), bg_status, LARGE),
        (f"{mode}: сильный статус на баре", colors.get("status_bar_strong", ""), bg_status, BODY),
        (f"{mode}: дим статуса на баре", colors.get("status_bar_dim", ""), bg_status, BODY),
        (f"{mode}: good на баре", colors.get("status_bar_good", ""), bg_status, LARGE),
        (f"{mode}: warn на баре", colors.get("status_bar_warn", ""), bg_status, LARGE),
        (f"{mode}: critical на баре", colors.get("status_bar_critical", ""), bg_status, LARGE),
        (f"{mode}: текст меню", colors.get("prompt", ""), bg_menu, BODY),
        (f"{mode}: текст на выборе", colors.get("prompt", ""), colors.get("selection_bg", ""), BODY),
        (f"{mode}: подпись сессии", colors.get("session_label", ""), bg_menu, BODY),
        (f"{mode}: метка ui_label", colors.get("ui_label", ""), bg_menu, BODY),
        (f"{mode}: ошибка", colors.get("ui_error", ""), bg_menu, LARGE),
    ]
    return pairs


def _map_pairs_legacy_removed() -> None:  # placeholder, см. _map_pairs(vars_, mode)
    raise NotImplementedError


def report() -> dict[str, Any]:
    theme = parse_theme(THEME)
    palettes = parse_map_palettes(PLUGIN)
    rows: list[dict[str, Any]] = []

    for mode in ("dark", "light"):
        colors = theme.get(mode) or {}
        if not colors:
            continue
        for label, fg, bg, limit in _theme_pairs(colors, mode):
            value = ratio(fg, bg)
            rows.append({"pair": label, "fg": fg, "bg": bg, "ratio": value, "limit": limit,
                         "kind": "текст" if limit >= BODY else "крупный",
                         "ok": bool(value and value >= limit)})

    for mode in ("light", "dark"):
        for label, fg, bg, limit in _map_pairs(palettes.get(mode) or {}, mode):
            value = ratio(fg, bg)
            rows.append({"pair": label, "fg": fg, "bg": bg, "ratio": value, "limit": limit,
                         "kind": "декор" if limit == 0 else "текст",
                         "ok": True if limit == 0 else bool(value and value >= limit)})

    failed = [row for row in rows if not row["ok"]]
    checked = [row for row in rows if row["limit"] > 0]
    return {"rows": rows, "failed": failed, "checked": len(checked), "total": len(rows),
            "worst": min((row["ratio"] for row in checked if row["ratio"]), default=0.0)}


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--fail-under", action="store_true",
                        help="код выхода 1, если есть пары ниже порога")
    args = parser.parse_args(argv)

    data = report()
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=1))
    else:
        for row in data["rows"]:
            mark = "✓" if row["ok"] else "✗"
            value = "—" if row["ratio"] is None else f"{row['ratio']:>5.2f}"
            need = "декор" if row["limit"] == 0 else f"{row['limit']:.1f}"
            print(f"{mark} {value} ({need})  {row['pair']}")
        if data["failed"]:
            print(f"\nпровалов: {len(data['failed'])} из {data['checked']} проверяемых · худший {data['worst']:.2f}")
        else:
            print(f"\nвсе {data['checked']} проверяемых пар проходят · худший {data['worst']:.2f}")
    return 1 if (data["failed"] and args.fail_under) else 0


if __name__ == "__main__":
    sys.exit(main())
