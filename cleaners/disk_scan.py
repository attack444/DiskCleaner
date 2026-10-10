"""Поиск больших файлов и пустых папок в заданной директории."""
from __future__ import annotations

import os
import heapq
from dataclasses import dataclass
from pathlib import Path


@dataclass
class BigFile:
    path: str
    size: int

    # Нужно для корректной работы heapq (сравнение объектов по размеру)
    def __lt__(self, other: BigFile) -> bool:
        return self.size < other.size


@dataclass
class EmptyFolder:
    path: str


def find_big_files(root: str, min_bytes: int, top: int = 200,
                   progress_cb=None, cancel=None) -> list[BigFile]:
    """Возвращает top самых больших файлов (>= min_bytes) в дереве root.
    
    Использует heapq для удержания только TOP-N элементов в памяти.
    """
    heap: list[BigFile] = []
    walked = 0
    
    # os.walk по умолчанию использует эффективный os.scandir под капотом
    for cur, dirs, files in os.walk(root):
        if cancel is not None and cancel.is_set():
            break
            
        # Исключаем симлинки из обхода
        dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(cur, d))]
        
        for name in files:
            if cancel is not None and cancel.is_set():
                return sorted(heap, key=lambda b: b.size, reverse=True)
                
            full = os.path.join(cur, name)
            try:
                # Быстрая проверка размера
                size = os.path.getsize(full)
            except OSError:
                continue
                
            if size >= min_bytes:
                new_file = BigFile(full, size)
                if len(heap) < top:
                    heapq.heappush(heap, new_file)
                elif size > heap[0].size:
                    heapq.heappushpop(heap, new_file)
                    
            walked += 1
            if progress_cb and walked % 50 == 0:
                progress_cb(walked, full)
                
    # Возвращаем отсортированный по убыванию результат
    return sorted(heap, key=lambda b: b.size, reverse=True)


def find_empty_folders(root: str, progress_cb=None, cancel=None,
                       min_depth: int = 1) -> list[EmptyFolder]:
    """Находит пустые папки в дереве root без использования рекурсии.

    Безопасен к RecursionError. Возвращает пути от самых глубоких.
    """
    empty: list[EmptyFolder] = []
    root_path = Path(root).resolve()
    
    # Используем стек для итеративного обхода (вместо рекурсии)
    # Храним кортеж: (текущий Path, обработан_ли_он)
    stack = [(root_path, False)]
    
    # Словарь для отслеживания: есть ли файлы в этой папке или её подпапках
    # По умолчанию считаем, что папка "чиста" (True), пока не доказано обратное
    folder_file_free: dict[Path, bool] = {}

    while stack:
        if cancel is not None and cancel.is_set():
            break
            
        cur, processed = stack.pop()
        
        if not processed:
            # Первый заход в папку: сканируем содержимое
            try:
                # os.scandir намного быстрее, чем os.listdir + os.path.isdir
                with os.scandir(cur) as entries:
                    subdirs = []
                    has_files = False
                    
                    for entry in entries:
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                subdirs.append(Path(entry.path))
                            else:
                                has_files = True
                        except OSError:
                            continue
                            
                    if has_files:
                        folder_file_free[cur] = False
                    else:
                        folder_file_free[cur] = True
                        
                    if subdirs:
                        # Возвращаем текущую папку в стек, отметив как "processed"
                        # Она закроется (проверится) ПОСЛЕ своих детей
                        stack.append((cur, True))
                        # Добавляем детей для сканирования
                        for sub in subdirs:
                            stack.append((sub, False))
                    else:
                        # Если подпапок нет, вычисляем глубину через pathlib
                        rel_depth = len(cur.parts) - len(root_path.parts)
                        if not has_files and rel_depth >= min_depth:
                            empty.append(EmptyFolder(str(cur)))
            except OSError:
                folder_file_free[cur] = False  # Нет доступа -> считаем непустой
        else:
            # Второй заход (пост-обработка): все дети этой папки уже обработаны
            try:
                with os.scandir(cur) as entries:
                    children_free = True
                    for entry in entries:
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                child_path = Path(entry.path)
                                # Если хотя бы один ребенок имеет файлы, то и родитель не чист
                                if not folder_file_free.get(child_path, False):
                                    children_free = False
                                    break
                        except OSError:
                            continue
                            
                    # Обновляем статус родителя на основе детей
                    if not children_free:
                        folder_file_free[cur] = False
                        
                    rel_depth = len(cur.parts) - len(root_path.parts)
                    if folder_file_free[cur] and rel_depth >= min_depth:
                        empty.append(EmptyFolder(str(cur)))
            except OSError:
                pass

    # Сортируем: самые глубокие папки (больше всего элементов в пути) будут первыми
    empty.sort(key=lambda e: len(Path(e.path).parts), reverse=True)
    return empty
