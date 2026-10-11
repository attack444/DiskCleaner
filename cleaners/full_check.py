"""Главный оркестратор: единый обход + интеллектуальные правила."""

from __future__ import annotations

import os
import time

from cleaners.utils import dir_size, human_size
from .disk_scan import unified_scan
from .duplicate_finder import DuplicateFinder
from .smart_rules import FileVerdict, PathContext, classify_path, judge_file
from .system_cleaner import SystemCleaner

RECENT_MODIFY_WINDOW = 7 * 24 * 60 * 60
STALE_DOWNLOAD_AGE = 90 * 24 * 60 * 60


def _scan_candidate(path: str, size: int) -> bool:
    return judge_file(path, size).verdict != FileVerdict.PROTECTED


def _inject_extra_junk(cleaner: SystemCleaner) -> None:
    local_app = os.environ.get("LOCALAPPDATA", "")
    app_data = os.environ.get("APPDATA", "")
    extra = {
        "tg": (
            os.path.join(app_data, "Telegram Desktop", "tdata", "user_data", "Cache"),
            "Кэш Telegram (медиафайлы)",
        ),
        "discord": (
            os.path.join(local_app, "Discord", "Cache"),
            "Кэш Discord (картинки и сессии)",
        ),
        "steam_web": (
            os.path.join(local_app, "Steam", "htmlcache"),
            "Кэш браузера Steam (CEF)",
        ),
        "epic_web": (
            os.path.join(local_app, "EpicGamesLauncher", "Saved", "webcache"),
            "Кэш Epic Games Launcher",
        ),
        "nvidia_shaders": (
            os.path.join(local_app, "NVIDIA", "GLCache"),
            "Кэш шейдеров NVIDIA (видеокарта)",
        ),
        "win_updates": (
            r"C:\Windows\SoftwareDistribution\Download",
            "Временный кэш обновлений Windows",
        ),
        "win_panther": (
            r"C:\Windows\Panther",
            "Остаточные логи обновлений Windows (Panther)",
        ),
    }
    for key, (p_path, title) in extra.items():
        if not os.path.isdir(p_path):
            continue
        cleaner.categories.append(
            type(
                "JunkCategory",
                (object,),
                {
                    "key": key,
                    "title": title,
                    "roots": [p_path],
                    "note": "Временный кэш.",
                    "kind": "dirs",
                    "size_info": lambda p=p_path: dir_size(p),
                    "exists": lambda: True,
                    "describe_roots": lambda p=p_path: p,
                },
            )()
        )


def run_full_check(
    cancel,
    q,
    root: str = "",
    *,
    min_dup_mb: float = 1.0,
    big_min_mb: float = 100.0,
    top: int = 400,
    skip_duplicates: bool = False,
    skip_empty: bool = False,
    skip_big: bool = False,
) -> dict:
    result: list[dict] = []
    summary: dict = {}
    processed_duplicate_paths: set[str] = set()
    target_path = root or os.environ.get("USERPROFILE", os.path.expanduser("~"))
    current_time = time.time()

    def emit(text: str):
        q.put(("progress", text))

    total_steps = 5
    step = 0

    def next_step(label: str):
        nonlocal step
        step += 1
        emit(f"{label}")

    # ---------- ЭТАП 1. Единый обход дерева ----------
    next_step(
        f"⚡ Шаг {step}/{total_steps}: Обход дерева (карта, крупные, "
        f"кандидаты на дубликаты, пустые папки)…"
    )

    def scan_progress(walked: int, path: str):
        if walked % 2000 == 0:
            emit(f"   Обработано объектов: {walked}")

    scan_data = unified_scan(
        target_path,
        big_min_bytes=0 if skip_big else int(big_min_mb * 1024 * 1024),
        dup_min_bytes=0 if skip_duplicates else int(min_dup_mb * 1024 * 1024),
        top=top,
        cancel=cancel,
        progress_cb=scan_progress,
        file_filter=_scan_candidate,
    )

    if cancel.is_set():
        return {"result": [], "summary": summary, "cancelled": True}

    extension_stats = scan_data["extension_stats"]
    big_files = scan_data["big_files"] if not skip_big else []
    files_by_size = scan_data["files_by_size"] if not skip_duplicates else {}
    empty_folders = scan_data["empty_folders"] if not skip_empty else []

    summary["extension_stats"] = extension_stats
    emit(
        f"   Готово: крупных — {len(big_files)}, групп по размеру — {len(files_by_size)}, "
        f"пустых — {len(empty_folders)}"
    )

    # ---------- ЭТАП 2. Мусор ----------
    next_step(f"🧹 Шаг {step}/{total_steps}: Сканирование кэша, логов и лаунчеров…")
    try:
        cleaner = SystemCleaner()
        _inject_extra_junk(cleaner)
        junk = cleaner.analyze()
    except Exception:
        junk = []

    junk_total = sum(r["size"] for r in junk if r["exists"])
    summary["junk_size"] = junk_total
    for rec in junk:
        if cancel.is_set():
            return {"result": [], "summary": summary, "cancelled": True}
        if not rec["exists"]:
            continue
        result.append(
            {
                "id": f"junk-{rec['key']}",
                "kind": "junk",
                "path": rec["roots"] or rec["title"],
                "size": rec["size"],
                "title": rec["title"],
                "sub": "🧹 Безопасный цифровой мусор. Отмечено для автоочистки.",
                "auto": True,
                "meta": {"key": rec["key"]},
            }
        )

    # ---------- ЭТАП 3. Дубликаты ----------
    dup_size = 0
    if skip_duplicates:
        emit(f"🔁 Шаг {step}/{total_steps}: Дубликаты — пропущено по профилю.")
    else:
        next_step(f"🔁 Шаг {step}/{total_steps}: Поиск точных дубликатов (MD5)…")
        safe_index: dict[int, list[str]] = {}
        for size, paths in files_by_size.items():
            kept = [
                p for p in paths if judge_file(p, size).verdict != FileVerdict.PROTECTED
            ]
            if len(kept) >= 2:
                safe_index[size] = kept

        dup_groups = []
        try:
            finder = DuplicateFinder(min_size=int(min_dup_mb * 1024 * 1024))
            dup_groups = finder.find_from_size_index(safe_index, cancel=cancel)
        except Exception:
            pass

        for gi, group in enumerate(dup_groups, start=1):
            if cancel.is_set():
                return {"result": [], "summary": summary, "cancelled": True}
            files = sorted(group.files)
            head_j = judge_file(files[0], group.size)
            if head_j.verdict == FileVerdict.PROTECTED:
                continue
            auto = head_j.verdict == FileVerdict.SAFE
            for fi, path in enumerate(files):
                if fi == 0:
                    continue
                p_j = judge_file(path, group.size)
                if p_j.verdict == FileVerdict.PROTECTED:
                    continue
                dup_size += group.size
                processed_duplicate_paths.add(path)
                result.append(
                    {
                        "id": f"dup-{gi}-{fi}",
                        "kind": "duplicate",
                        "path": path,
                        "size": group.size,
                        "title": f"Копия файла: {os.path.basename(path)}",
                        "sub": (
                            "🔁 Точный дубликат. Оригинал в безопасности."
                            if auto
                            else "⚠️ Дубликат в папке приложения/данных — проверьте вручную."
                        ),
                        "auto": auto,
                        "meta": {"group": files},
                    }
                )

    summary["dup_size"] = dup_size

    # ---------- ЭТАП 4. Крупные файлы ----------
    big_size = 0
    if skip_big:
        emit(f"🗜 Шаг {step}/{total_steps}: Крупные файлы — пропущено по профилю.")
    else:
        next_step(f"🗜 Шаг {step}/{total_steps}: Разбор крупных объектов…")
        for i, bf in enumerate(big_files):
            if cancel.is_set():
                return {"result": [], "summary": summary, "cancelled": True}
            path_low = bf.path.lower()
            if bf.path in processed_duplicate_paths:
                continue
            ctx = classify_path(bf.path)
            j = judge_file(bf.path, bf.size, ctx)
            if j.verdict == FileVerdict.PROTECTED:
                continue

            auto_check = False
            risk_sub = j.reason
            _, ext = os.path.splitext(path_low)
            try:
                mtime = os.path.getmtime(bf.path)
                age = current_time - mtime
                if "\\downloads\\" in path_low and age > STALE_DOWNLOAD_AGE:
                    auto_check = True
                    risk_sub = (
                        "📥 Забытый дистрибутив / архив в Загрузках (>3 месяцев)."
                    )
                elif ctx in (PathContext.USER_MEDIA, PathContext.USER_DATA):
                    if any(
                        k in path_low
                        for k in (
                            "\\captures\\",
                            "\\obs\\",
                            "\\screenshots\\",
                            "\\recordings\\",
                        )
                    ):
                        risk_sub = "🎬 Запись экрана или скриншот — проверьте вручную."
                    else:
                        risk_sub = "📁 Личный файл пользователя — решение за вами."
                elif j.verdict == FileVerdict.SAFE:
                    auto_check = True
            except OSError:
                pass

            big_size += bf.size
            result.append(
                {
                    "id": f"big-{i}",
                    "kind": "big",
                    "path": bf.path,
                    "size": bf.size,
                    "title": f"Крупный объект: {os.path.basename(bf.path)}",
                    "sub": f"Размер: {human_size(bf.size)} · {risk_sub}",
                    "auto": auto_check,
                    "meta": {"context": ctx.value},
                }
            )

    summary["big_size"] = big_size

    # ---------- ЭТАП 5. Пустые папки ----------
    if skip_empty:
        emit(f"📂 Шаг {step}/{total_steps}: Пустые папки — пропущено по профилю.")
    else:
        next_step(f"📂 Шаг {step}/{total_steps}: Обработка пустых папок…")
        for i, ef in enumerate(empty_folders):
            if cancel.is_set():
                return {"result": [], "summary": summary, "cancelled": True}
            ctx = classify_path(ef.path)
            if ctx in (PathContext.SYSTEM, PathContext.GAME_LIBRARY):
                continue
            auto = ctx in (
                PathContext.TEMP,
                PathContext.USER_DATA,
                PathContext.USER_MEDIA,
            )
            result.append(
                {
                    "id": f"empty-{i}",
                    "kind": "empty",
                    "path": ef.path,
                    "size": 0,
                    "title": f"Пустая папка: {os.path.basename(ef.path)}",
                    "sub": (
                        "📂 Пустая структура после старого софта. Можно безопасно стереть."
                        if auto
                        else "⚠️ Пустая папка внутри приложения — удаляйте осознанно."
                    ),
                    "auto": auto,
                    "meta": {"context": ctx.value},
                }
            )

    summary["total_size"] = junk_total + dup_size + big_size
    summary["total_items"] = len(result)
    return {"result": result, "summary": summary, "cancelled": False}
