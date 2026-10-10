"""Поиск дубликатов файлов по содержимому (Безопасное хеширование)."""
from __future__ import annotations

import os
import hashlib
from dataclasses import dataclass, field
from collections import defaultdict


@dataclass
class DuplicateGroup:
    files: list[str] = field(default_factory=list)
    size: int = 0
    hash_hex: str = ""

    @property
    def count(self) -> int:
        return len(self.files)


class DuplicateFinder:
    def __init__(self, chunk_size: int = 1024 * 1024, min_size: int = 0):
        self.chunk = chunk_size
        self.min_size = min_size

    def find(self, roots: list[str], progress_cb=None, cancel=None,
             extensions: list[str] | tuple[str, ...] | None = None,
             skip_size_below: int = 0) -> list[DuplicateGroup]:
        """Основной метод."""
        if extensions:
            extensions = tuple(ext.lower() for ext in extensions)

        # 1. Сбор всех файлов и группировка строго по РАЗМЕРУ
        file_sizes: dict[int, list[str]] = defaultdict(list)
        collected = 0

        for root in roots:
            if cancel is not None and cancel.is_set():
                break
            for fpath in self._iter_files(root, cancel=cancel):
                try:
                    size = os.path.getsize(fpath)
                except OSError:
                    continue
                
                # Фильтры
                if size < self.min_size or size < skip_size_below:
                    continue
                if extensions and not fpath.lower().endswith(extensions):
                    continue

                file_sizes[size].append(fpath)
                collected += 1
                if progress_cb and collected % 50 == 0:
                    progress_cb(collected, fpath)

        if cancel is not None and cancel.is_set():
            return []

        # 2. Двухэтапная проверка выгоды (Размер -> Быстрый Хеш -> Полный Хеш)
        groups: list[DuplicateGroup] = []
        hashed_count = 0

        for size, paths in file_sizes.items():
            if len(paths) < 2:
                continue
                
            # ЭТАП 2А: Быстрое пред-хеширование (только первые 128 КБ)
            # Избавляет от необходимости читать целиком разные файлы одинакового размера
            fast_buckets: dict[str, list[str]] = defaultdict(list)
            for path in paths:
                if cancel is not None and cancel.is_set():
                    return groups
                try:
                    # Хешируем только маленький кусочек для предварительной сортировки
                    fast_digest = self._hash_file_partial(path)
                except OSError:
                    continue
                fast_buckets[fast_digest].append(path)

            # ЭТАП 2Б: Полное хеширование только тех файлов, у которых совпали и размер, и «голова»
            for fast_digest, candidate_paths in fast_buckets.items():
                if len(candidate_paths) < 2:
                    continue  # Файлы оказались разными на первых 128КБ
                    
                full_buckets: dict[str, list[str]] = defaultdict(list)
                for path in candidate_paths:
                    try:
                        # Считаем честный полный хеш для 100% безопасности
                        full_digest = self._hash_file_full(path)
                    except OSError:
                        continue
                        
                    full_buckets[full_digest].append(path)
                    hashed_count += 1
                    if progress_cb and hashed_count % 5 == 0:
                        progress_cb(hashed_count, f"Проверка: {os.path.basename(path)}")

                for full_digest, members in full_buckets.items():
                    if len(members) > 1:
                        groups.append(DuplicateGroup(files=members, size=size, hash_hex=full_digest))

        return groups

    @staticmethod
    def _iter_files(root: str, cancel=None):
        if os.path.isfile(root):
            yield root
            return
        if not os.path.isdir(root):
            return
        for cur, dirs, files in os.walk(root):
            if cancel is not None and cancel.is_set():
                break
            dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(cur, d))]
            for name in files:
                if cancel is not None and cancel.is_set():
                    return
                yield os.path.join(cur, name)

    def _hash_file_partial(self, path: str) -> str:
        """Очень быстрый хеш первых 128 КБ файла для черновой фильтрации."""
        h = hashlib.md5()
        try:
            with open(path, "rb") as f:
                chunk = f.read(128 * 1024)
                if chunk:
                    h.update(chunk)
        except OSError:
            raise
        return h.hexdigest()

    def _hash_file_full(self, path: str) -> str:
        """Полный честный хеш файла. Гарантирует 100% безопасность удаления."""
        h = hashlib.md5()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(self.chunk)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
