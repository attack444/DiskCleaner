"""Генератор HTML- и CSV-отчётов по результатам анализа диска."""

from __future__ import annotations

import csv
import html
import os
import tempfile
import time
import webbrowser

from .utils import human_size


_KIND_LABELS = {
    "junk": "🧹 Мусор",
    "duplicate": "🔁 Дубликат",
    "big": "🗜 Крупный объект",
    "empty": "📂 Пустая папка",
}


_CSS = """
body { font-family: 'Segoe UI', Roboto, system-ui, sans-serif;
       background: #1a1a1a; color: #e8e8e8; margin: 0; padding: 32px; }
h1 { color: #00ff66; margin: 0 0 4px 0; font-size: 26px; }
h2 { color: #00ff66; margin: 32px 0 12px 0; font-size: 18px; border-bottom: 1px solid #333; padding-bottom: 6px; }
.sub { color: #888; font-size: 13px; margin-bottom: 24px; }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin: 16px 0 32px; }
.card { background: #242424; border: 1px solid #333; border-radius: 8px; padding: 16px; }
.card .label { color: #999; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; }
.card .value { color: #00ff66; font-size: 24px; font-weight: 700; margin-top: 4px; }
table { width: 100%; border-collapse: collapse; margin: 8px 0 24px; font-size: 13px; }
th { background: #232323; color: #00ff66; text-align: left; padding: 10px 12px; border-bottom: 2px solid #333; font-weight: 600; position: sticky; top: 0; }
td { padding: 8px 12px; border-bottom: 1px solid #262626; word-break: break-all; }
tr:hover td { background: #202020; }
td.size { text-align: right; color: #60cdff; white-space: nowrap; font-variant-numeric: tabular-nums; }
.tag { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }
.tag.junk { background: #1e3a1e; color: #7fff7f; }
.tag.duplicate { background: #2a2a1e; color: #d4d47f; }
.tag.big { background: #3a2a1e; color: #ffcc66; }
.tag.empty { background: #1e2a3a; color: #7fc2ff; }
.protected { color: #ff8844; }
.footer { color: #666; font-size: 11px; margin-top: 40px; text-align: center; }
.section-note { color: #888; font-size: 12px; margin-bottom: 8px; }
"""


def _esc(s: str) -> str:
    return html.escape(str(s), quote=True)


def _tag(kind: str) -> str:
    label = _KIND_LABELS.get(kind, kind)
    return f'<span class="tag {kind}">{_esc(label)}</span>'


def _summary_cards(summary: dict, total_items: int) -> str:
    ext = summary.get("extension_stats", {}) or {}
    cards = [
        ("Всего найдено", human_size(summary.get("total_size", 0))),
        ("Объектов", f"{total_items}"),
        ("Мусор", human_size(summary.get("junk_size", 0))),
        ("Дубликаты", human_size(summary.get("dup_size", 0))),
        ("Крупные файлы", human_size(summary.get("big_size", 0))),
    ]
    html_cards = []
    for label, value in cards:
        html_cards.append(
            f'<div class="card"><div class="label">{_esc(label)}</div>'
            f'<div class="value">{_esc(value)}</div></div>'
        )
    if ext:
        for name in ("Видео", "Изображения", "Документы", "Архивы", "Другое"):
            if name in ext:
                html_cards.append(
                    f'<div class="card"><div class="label">{_esc(name)}</div>'
                    f'<div class="value">{_esc(human_size(ext[name]))}</div></div>'
                )
    return '<div class="cards">' + "".join(html_cards) + "</div>"


def _items_table(items: list[dict], show_protected: bool = True) -> str:
    if not items:
        return '<p class="section-note">Нет объектов в этой категории.</p>'
    rows = []
    for it in items:
        path = _esc(it.get("path", ""))
        size = human_size(int(it.get("size", 0) or 0))
        kind = it.get("kind", "")
        sub = it.get("sub", "")
        prot = bool(it.get("protected"))
        if prot and not show_protected:
            continue
        cls_path = ' class="protected"' if prot else ""
        note = _esc(sub) if sub else ("защищён" if prot else "—")
        rows.append(
            f"<tr><td>{_tag(kind)}</td>"
            f"<td{cls_path}>{path}</td>"
            f'<td class="size">{_esc(size)}</td>'
            f"<td>{note}</td></tr>"
        )
    if not rows:
        return '<p class="section-note">Нет объектов в этой категории.</p>'
    return (
        "<table><thead><tr>"
        '<th style="width:140px">Тип</th>'
        "<th>Объект</th>"
        '<th style="width:100px; text-align:right">Размер</th>'
        '<th style="width:280px">Примечание</th>'
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )


def write_html_report(
    path: str,
    *,
    title: str = "Отчёт DiskCleaner",
    root_path: str = "",
    profile: str = "",
    summary: dict | None = None,
    items: list[dict] | None = None,
    cancelled: bool = False,
) -> str:
    """Создаёт HTML-отчёт. Возвращает путь к файлу."""
    summary = summary or {}
    items = items or []

    groups = {"junk": [], "duplicate": [], "big": [], "empty": [], "other": []}
    for it in items:
        groups.setdefault(it.get("kind", "other"), []).append(it)

    now = time.strftime("%d.%m.%Y %H:%M:%S")
    parts = [
        "<!DOCTYPE html><html lang='ru'><head><meta charset='utf-8'>",
        f"<title>{_esc(title)}</title>",
        f"<style>{_CSS}</style></head><body>",
        f"<h1>{_esc(title)}</h1>",
        f"<div class='sub'>Дата: {_esc(now)} · Цель: <code>{_esc(root_path)}</code>",
    ]
    if profile:
        parts.append(f" · Профиль: <b>{_esc(profile)}</b>")
    if cancelled:
        parts.append(" · <b style='color:#ff8844'>СКАНИРОВАНИЕ ОТМЕНЕНО</b>")
    parts.append("</div>")

    parts.append(_summary_cards(summary, len(items)))

    parts.append("<h2>🧹 Цифровой мусор (кэш, логи, временные файлы)</h2>")
    parts.append(_items_table(groups.get("junk", [])))

    parts.append("<h2>🔁 Дубликаты (точные копии)</h2>")
    parts.append(_items_table(groups.get("duplicate", [])))

    parts.append("<h2>🗜 Крупные объекты</h2>")
    parts.append(_items_table(groups.get("big", [])))

    parts.append("<h2>📂 Пустые папки</h2>")
    parts.append(_items_table(groups.get("empty", [])))

    if groups.get("other"):
        parts.append("<h2>Прочее</h2>")
        parts.append(_items_table(groups["other"]))

    parts.append(
        "<div class='footer'>Сформировано приложением DiskCleaner. "
        "Файлы, отправленные в Корзину, можно восстановить через проводник Windows.</div>"
    )
    parts.append("</body></html>")

    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("".join(parts))
    return path


def write_csv_report(path: str, items: list[dict]) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Тип", "Путь", "Размер (байт)", "Размер", "Примечание"])
        for it in items:
            w.writerow(
                [
                    _KIND_LABELS.get(it.get("kind", ""), it.get("kind", "")),
                    it.get("path", ""),
                    int(it.get("size", 0) or 0),
                    human_size(int(it.get("size", 0) or 0)),
                    it.get("sub", ""),
                ]
            )
    return path


def reports_dir() -> str:
    base = os.path.join(
        os.environ.get("TEMP", tempfile.gettempdir()),
        "DiskCleaner",
        "reports",
    )
    os.makedirs(base, exist_ok=True)
    return base


def default_report_path(ext: str = "html") -> str:
    ts = time.strftime("%Y%m%d_%H%M%S")
    return os.path.join(reports_dir(), f"report_{ts}.{ext}")


def open_in_browser(path: str) -> bool:
    try:
        webbrowser.open("file:///" + os.path.abspath(path).replace("\\", "/"))
        return True
    except Exception:
        return False
