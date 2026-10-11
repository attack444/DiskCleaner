"""Поиск больших файлов, пустых папок и единый обход дерева."""

from __future__ import annotations

import heapq
import os
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path


@dataclass
class BigFile:
    path: str
    size: int

    def __lt__(self, other: "BigFile") -> bool:
        return self.size < other.size


@dataclass
class EmptyFolder:
    path: str


_EXT_CATEGORIES: dict[str, set[str]] = {
    "Видео": {".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flv", ".webm"},
    "Изображения": {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".heic"},
    "Документы": {".pdf", ".docx", ".doc", ".txt", ".xlsx", ".xls", ".pptx"},
    "Архивы": {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2"},
}


def _ext_category(ext: str) -> str:
    for cat, exts in _EXT_CATEGORIES.items():
        if ext in exts:
            return cat
    return "Другое"


# --------------------------------------------------------------------------
# Единый обход дерева
# --------------------------------------------------------------------------


def unified_scan(
    root: str,
    *,
    big_min_bytes: int = 0,
    dup_min_bytes: int = 0,
    top: int = 200,
    cancel=None,
    progress_cb=None,
    file_filter=None,  # ← колбэк (path, size) -> bool
) -> dict:
    """Один проход по дереву. Возвращает карту памяти, крупные файлы,
    кандидатов на дубликаты и пустые папки.

    file_filter(path, size): если возвращает False — файл НЕ попадает
    в big_files и files_by_size. Расширения учитываются всегда.
    """
    extension_stats: dict[str, int] = {
        "Видео": 0,
        "Изображения": 0,
        "Документы": 0,
        "Архивы": 0,
        "Другое": 0,
    }
    big_heap: list[BigFile] = []
    files_by_size: dict[int, list[str]] = defaultdict(list)

    dir_has_files: dict[str, bool] = {}
    dir_subdirs: dict[str, list[str]] = {}

    root_abs = os.path.abspath(root)
    root_path = Path(root_abs)
    root_depth = len(root_path.parts)

    # Одиночный файл
    if os.path.isfile(root_abs):
        try:
            size = os.path.getsize(root_abs)
            _, ext = os.path.splitext(root_abs.lower())
            extension_stats[_ext_category(ext)] += size
            keep = file_filter is None or file_filter(root_abs, size)
            if keep:
                if big_min_bytes and size >= big_min_bytes:
                    big_heap.append(BigFile(root_abs, size))
                if dup_min_bytes and size >= dup_min_bytes:
                    files_by_size[size].append(root_abs)
        except OSError:
            pass
        return {
            "extension_stats": extension_stats,
            "big_files": sorted(big_heap, key=lambda b: b.size, reverse=True),
            "files_by_size": dict(files_by_size),
            "empty_folders": [],
        }

    if not os.path.isdir(root_abs):
        return {
            "extension_stats": extension_stats,
            "big_files": [],
            "files_by_size": {},
            "empty_folders": [],
        }

    stack: list[str] = [root_abs]
    walked = 0

    while stack:
        if cancel is not None and cancel.is_set():
            break

        cur = stack.pop()
        has_files = False
        subdirs: list[str] = []

        try:
            with os.scandir(cur) as it:
                for entry in it:
                    if cancel is not None and cancel.is_set():
                        break
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            subdirs.append(entry.path)
                            stack.append(entry.path)
                            continue
                        if not entry.is_file(follow_symlinks=False):
                            continue

                        has_files = True
                        size = entry.stat(follow_symlinks=False).st_size

                        # Карта памяти — учитываем ВСЕ файлы
                        _, ext = os.path.splitext(entry.name.lower())
                        extension_stats[_ext_category(ext)] += size

                        # big/dup — только если прошли фильтр
                        keep = True
                        if file_filter is not None:
                            try:
                                keep = bool(file_filter(entry.path, size))
                            except Exception:
                                keep = False

                        if keep:
                            if big_min_bytes and size >= big_min_bytes:
                                bf = BigFile(entry.path, size)
                                if len(big_heap) < top:
                                    heapq.heappush(big_heap, bf)
                                elif size > big_heap[0].size:
                                    heapq.heappushpop(big_heap, bf)
                            if dup_min_bytes and size >= dup_min_bytes:
                                files_by_size[size].append(entry.path)

                        walked += 1
                        if progress_cb and walked % 500 == 0:
                            progress_cb(walked, entry.path)
                    except OSError:
                        continue
        except OSError:
            has_files = True

        dir_has_files[cur] = has_files
        dir_subdirs[cur] = subdirs

    # Восстановление пустых папок (без повторного scandir)
    is_empty: dict[str, bool] = {}
    empty_folders: list[EmptyFolder] = []

    all_dirs = sorted(
        dir_has_files.keys(), key=lambda p: len(Path(p).parts), reverse=True
    )
    for d in all_dirs:
        if dir_has_files.get(d, True):
            is_empty[d] = False
            continue
        subdirs = dir_subdirs.get(d, [])
        if all(is_empty.get(s, False) for s in subdirs):
            is_empty[d] = True
            rel_depth = len(Path(d).parts) - root_depth
            if rel_depth >= 1:
                empty_folders.append(EmptyFolder(d))
        else:
            is_empty[d] = False

    dir_has_files.clear()
    dir_subdirs.clear()

    return {
        "extension_stats": extension_stats,
        "big_files": sorted(big_heap, key=lambda b: b.size, reverse=True),
        "files_by_size": dict(files_by_size),
        "empty_folders": empty_folders,
    }


# --------------------------------------------------------------------------
# Публичные обёртки
# --------------------------------------------------------------------------


def find_big_files(
    root: str,
    min_bytes: int,
    top: int = 200,
    progress_cb=None,
    cancel=None,
    file_filter=None,
) -> list[BigFile]:
    heap: list[BigFile] = []
    walked = 0
    stack = [os.path.abspath(root)]

    while stack:
        if cancel is not None and cancel.is_set():
            break
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for entry in it:
                    if cancel is not None and cancel.is_set():
                        break
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                            continue
                        if not entry.is_file(follow_symlinks=False):
                            continue
                        size = entry.stat(follow_symlinks=False).st_size
                        if size >= min_bytes:
                            if file_filter is not None:
                                try:
                                    if not file_filter(entry.path, size):
                                        continue
                                except Exception:
                                    continue
                            bf = BigFile(entry.path, size)
                            if len(heap) < top:
                                heapq.heappush(heap, bf)
                            elif size > heap[0].size:
                                heapq.heappushpop(heap, bf)
                        walked += 1
                        if progress_cb and walked % 200 == 0:
                            progress_cb(walked, entry.path)
                    except OSError:
                        continue
        except OSError:
            continue

    return sorted(heap, key=lambda b: b.size, reverse=True)


def find_empty_folders(
    root: str, progress_cb=None, cancel=None, min_depth: int = 1
) -> list[EmptyFolder]:
    res = unified_scan(root, cancel=cancel, progress_cb=progress_cb)
    if min_depth <= 1:
        return res["empty_folders"]
    root_parts = len(Path(os.path.abspath(root)).parts)
    return [
        ef
        for ef in res["empty_folders"]
        if len(Path(ef.path).parts) - root_parts >= min_depth
    ]


def analyze_extension_types(root: str, cancel=None) -> dict[str, int]:
    res = unified_scan(root, cancel=cancel)
    return res["extension_stats"]
