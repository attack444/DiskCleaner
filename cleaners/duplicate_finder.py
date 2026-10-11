"""Поиск дубликатов файлов по содержимому (безопасный и оптимизированный)."""

from __future__ import annotations

import hashlib
import os
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

PRESETS = {
    "images": (".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".heic"),
    "documents": (".pdf", ".docx", ".doc", ".txt", ".xlsx", ".xls", ".pptx", ".odt"),
    "media": (".mp4", ".avi", ".mkv", ".mp3", ".wav", ".flac", ".mov", ".wmv"),
    "archives": (".zip", ".rar", ".7z", ".tar", ".gz"),
}

# Точные имена компонентов пути, которые пропускаем целиком
_IGNORE_COMPONENTS = {
    "windows",
    "program files",
    "program files (x86)",
    "programdata",
    "system volume information",
    "$recycle.bin",
    "__pycache__",
    ".git",
    ".venv",
    "node_modules",
}

# Много-компонентные подпути (проверяются последовательным сравнением)
_IGNORE_SUBPATHS = (
    ("appdata", "local", "microsoft"),
    ("appdata", "roaming", "microsoft"),
)

IGNORE_EXTS = {
    ".exe",
    ".dll",
    ".sys",
    ".lnk",
    ".ini",
    ".inf",
    ".msi",
    ".pyc",
    ".pyd",
}

_PARTIAL_HEAD = 128 * 1024
_PARTIAL_TAIL = 64 * 1024


@dataclass
class DuplicateGroup:
    files: list[str] = field(default_factory=list)
    size: int = 0
    hash_hex: str = ""

    @property
    def count(self) -> int:
        return len(self.files)


def _should_skip_dir(path: str) -> bool:
    parts = tuple(p.lower() for p in Path(path).parts)
    for p in parts:
        if p in _IGNORE_COMPONENTS:
            return True
    if len(parts) < 2:
        return False
    for sub in _IGNORE_SUBPATHS:
        n = len(sub)
        for i in range(len(parts) - n + 1):
            if parts[i : i + n] == sub:
                return True
    return False


class DuplicateFinder:
    def __init__(self, chunk_size: int = 1024 * 1024, min_size: int = 4096):
        self.chunk = chunk_size
        self.min_size = max(min_size, 4096)  # 0-байтовые дубликаты не нужны

    # ----------------------------------------------------------------------
    # Публичный API
    # ----------------------------------------------------------------------

    def find(
        self,
        roots: list[str],
        progress_cb=None,
        cancel=None,
        extensions: list[str] | tuple[str, ...] | None = None,
        skip_size_below: int = 0,
    ) -> list[DuplicateGroup]:
        """Собирает файлы сам и делегирует хеширование в find_from_size_index."""
        if extensions:
            extensions = tuple(ext.lower() for ext in extensions)

        files_by_size: dict[int, list[str]] = defaultdict(list)
        collected = 0

        for root in roots:
            if cancel is not None and cancel.is_set():
                break
            for fpath in self._iter_files(root, cancel=cancel):
                try:
                    size = os.path.getsize(fpath)
                except OSError:
                    continue
                if size < self.min_size or size < skip_size_below:
                    continue
                if extensions and not fpath.lower().endswith(extensions):
                    continue
                files_by_size[size].append(fpath)
                collected += 1
                if progress_cb and collected % 50 == 0:
                    progress_cb(collected, fpath)

        if cancel is not None and cancel.is_set():
            return []

        return self.find_from_size_index(
            files_by_size, progress_cb=progress_cb, cancel=cancel
        )

    def find_from_size_index(
        self,
        files_by_size: dict[int, list[str]],
        progress_cb=None,
        cancel=None,
    ) -> list[DuplicateGroup]:
        """Принимает уже готовый индекс {size: [paths]}. Делает двухэтапное хеширование."""
        groups: list[DuplicateGroup] = []
        hashed_count = 0

        for size, paths in files_by_size.items():
            if cancel is not None and cancel.is_set():
                return groups
            if len(paths) < 2:
                continue

            # ЭТАП 2А: быстрый хеш головы+хвоста
            fast_buckets: dict[str, list[str]] = defaultdict(list)
            for path in paths:
                if cancel is not None and cancel.is_set():
                    return groups
                try:
                    fast_digest = self._hash_file_partial(path)
                except OSError:
                    continue
                fast_buckets[fast_digest].append(path)

            # ЭТАП 2Б: полный MD5 для реальных кандидатов
            for _fast_digest, candidates in fast_buckets.items():
                if len(candidates) < 2:
                    continue
                full_buckets: dict[str, list[str]] = defaultdict(list)
                for path in candidates:
                    if cancel is not None and cancel.is_set():
                        return groups
                    try:
                        full_digest = self._hash_file_full(path)
                    except OSError:
                        continue
                    full_buckets[full_digest].append(path)
                    hashed_count += 1
                    if progress_cb and hashed_count % 5 == 0:
                        progress_cb(
                            hashed_count,
                            f"Проверка содержимого: {os.path.basename(path)}",
                        )

                for full_digest, members in full_buckets.items():
                    if len(members) > 1:
                        groups.append(
                            DuplicateGroup(
                                files=members, size=size, hash_hex=full_digest
                            )
                        )

        return groups

    # ----------------------------------------------------------------------
    # Обход файлов
    # ----------------------------------------------------------------------

    @staticmethod
    def _iter_files(root: str, cancel=None):
        """Безопасный генератор: точные имена компонентов пути + симлинки."""
        if os.path.isfile(root):
            _, ext = os.path.splitext(root.lower())
            if ext not in IGNORE_EXTS:
                yield root
            return
        if not os.path.isdir(root):
            return

        for cur, dirs, files in os.walk(root):
            if cancel is not None and cancel.is_set():
                return
            if _should_skip_dir(cur):
                dirs[:] = []
                continue
            # Не заходим в симлинки и в игнорируемые компоненты
            dirs[:] = [
                d
                for d in dirs
                if not os.path.islink(os.path.join(cur, d))
                and d.lower() not in _IGNORE_COMPONENTS
            ]
            for name in files:
                if cancel is not None and cancel.is_set():
                    return
                _, ext = os.path.splitext(name.lower())
                if ext in IGNORE_EXTS:
                    continue
                yield os.path.join(cur, name)

    # ----------------------------------------------------------------------
    # Хеширование
    # ----------------------------------------------------------------------

    def _hash_file_partial(self, path: str) -> str:
        """Голова (128 КБ) + хвост (64 КБ) + размер — ловит одинаковые контейнеры с общим header."""
        size = os.path.getsize(path)
        h = hashlib.md5()
        with open(path, "rb") as f:
            h.update(f.read(_PARTIAL_HEAD))
            if size > _PARTIAL_HEAD + _PARTIAL_TAIL:
                f.seek(-_PARTIAL_TAIL, os.SEEK_END)
                h.update(f.read(_PARTIAL_TAIL))
        h.update(str(size).encode())
        return h.hexdigest()

    def _hash_file_full(self, path: str) -> str:
        h = hashlib.md5()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(self.chunk)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
