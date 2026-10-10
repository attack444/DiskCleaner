"""«Полная проверка»: объединяет все сканеры и возвращает единый список находок.

Запуск — одна кнопка в UI.
"""
from __future__ import annotations

import os
from .system_cleaner import SystemCleaner
from .duplicate_finder import DuplicateFinder
from .disk_scan import find_big_files, find_empty_folders
from . import utils


def run_full_check(cancel, q, root: str = "", min_dup_mb: float = 1.0,
                   big_min_mb: float = 100.0, top: int = 300) -> dict:
    """Выполняет все этапы проверки и возвращает сводный результат.

    cancel — threading.Event (отмена), q — очередь прогресса (put(("progress", str))).
    """
    result: list[dict] = []
    summary: dict = {}
    
    # Множество путей, которые уже добавлены как дубликаты, 
    # чтобы не учитывать их объем повторно в категории "Большие файлы"
    processed_duplicate_paths: set[str] = set()

    def emit(text: str):
        q.put(("progress", text))

    def emit_error(text: str):
        # Отдельный маркер для UI, чтобы подсветить ошибку красным, если нужно
        q.put(("error", text))

    # --- 1. Системный мусор ---
    emit("Этап 1/4: анализ системного мусора…")
    try:
        cleaner = SystemCleaner()
        junk = cleaner.analyze()
    except Exception as e:
        emit_error(f"⚠️ Ошибка при анализе системного мусора: {e}")
        junk = []

    junk_total = sum(r["size"] for r in junk if r["exists"])
    summary["junk_size"] = junk_total
    summary["junk_cats"] = len([r for r in junk if r["exists"]])
    
    for rec in junk:
        if cancel.is_set():
            return {"result": [], "summary": summary, "cancelled": True}
        if not rec["exists"]:
            continue
        result.append({
            "id": f"junk-{rec['key']}",
            "kind": "junk",
            "path": rec["roots"] or rec["title"],
            "size": rec["size"],
            "title": rec["title"],
            "sub": f"Размер: {utils.human_size(rec['size'])}",
            "auto": True,
            "meta": {"key": rec["key"]},
        })

    if cancel.is_set():
        return {"result": [], "summary": summary, "cancelled": True}

    # --- 2. Дубликаты ---
    emit("Этап 2/4: поиск дубликатов…")
    dup_target = root or os.path.expanduser("~")
    dup_groups = []
    try:
        finder = DuplicateFinder(min_size=int(min_dup_mb * 1024 * 1024))
        dup_groups = finder.find([dup_target], cancel=cancel)
    except Exception as e:
        emit_error(f"⚠️ Ошибка при поиске дубликатов: {e}")

    dup_size = 0
    dup_count = 0
    for gi, group in enumerate(dup_groups, start=1):
        if cancel.is_set():
            return {"result": [], "summary": summary, "cancelled": True}
            
        files = sorted(group.files)
        # «оригинал» — первый (fi == 0); остальные — кандидаты на удаление
        for fi, path in enumerate(files):
            if fi == 0:
                continue
            dup_size += group.size
            dup_count += 1
            processed_duplicate_paths.add(path) # Запоминаем путь дубликата
            
            result.append({
                "id": f"dup-{gi}-{fi}",
                "kind": "duplicate",
                "path": path,
                "size": group.size,
                "title": f"Дубликат: {os.path.basename(path)}",
                "sub": f"Группа №{gi} · идентичен «{os.path.basename(files[0])}»",
                "auto": True,
                "meta": {"group": files},
            })
            
    summary["dup_size"] = dup_size
    summary["dup_count"] = dup_count

    if cancel.is_set():
        return {"result": [], "summary": summary, "cancelled": True}

    # --- 3. Большие файлы ---
    emit("Этап 3/4: поиск больших файлов…")
    big_root = root or os.path.expanduser("~")
    big_files = []
    try:
        big_min = int(big_min_mb * 1024 * 1024)
        big_files = find_big_files(big_root, big_min, top=top, cancel=cancel)
    except Exception as e:
        emit_error(f"⚠️ Ошибка при поиске больших файлов: {e}")

    big_size = 0
    big_count = 0
    for i, bf in enumerate(big_files):
        if cancel.is_set():
            return {"result": [], "summary": summary, "cancelled": True}
            
        # Если этот файл уже помечен как удаляемый дубликат, 
        # мы все равно покажем его в списке больших, но его размер в big_size не плюсуем,
        # чтобы избежать двойного учета в общем итоге.
        if bf.path not in processed_duplicate_paths:
            big_size += bf.size
            
        big_count += 1
        result.append({
            "id": f"big-{i}",
            "kind": "big",
            "path": bf.path,
            "size": bf.size,
            "title": f"Большой файл: {os.path.basename(bf.path)}",
            "sub": f"Размер: {utils.human_size(bf.size)}",
            "auto": False, # Большие файлы удалять автоматически нельзя!
            "meta": {},
        })
        
    summary["big_size"] = big_size
    summary["big_count"] = big_count

    if cancel.is_set():
        return {"result": [], "summary": summary, "cancelled": True}

    # --- 4. Пустые папки ---
    emit("Этап 4/4: поиск пустых папок…")
    empty_root = root or os.path.expanduser("~")
    empty_folders = []
    try:
        empty_folders = find_empty_folders(empty_root, cancel=cancel)
    except Exception as e:
        emit_error(f"⚠️ Ошибка при поиске пустых папок: {e}")

    summary["empty_count"] = len(empty_folders)
    for i, ef in enumerate(empty_folders):
        if cancel.is_set():
            return {"result": [], "summary": summary, "cancelled": True}
        result.append({
            "id": f"empty-{i}",
            "kind": "empty",
            "path": ef.path,
            "size": 0,
            "title": f"Пустая папка: {os.path.basename(ef.path)}",
            "sub": ef.path,
            "auto": True,
            "meta": {},
        })

    # Корректный расчет общего размера потенциально освобождаемого места
    # (Junk + Дубликаты + Большие файлы, которые НЕ являются дубликатами)
    summary["total_size"] = junk_total + dup_size + big_size
    summary["total_items"] = len(result)
    
    return {"result": result, "summary": summary, "cancelled": False}
