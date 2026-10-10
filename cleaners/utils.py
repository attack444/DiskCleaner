"""Общие утилиты: форматирование размеров, подсчёт, удаление в корзину Windows.

Использует исключительно стандартную библиотеку Python + Win32 API через ctypes.
"""
from __future__ import annotations

import os
import shutil
import ctypes
from ctypes import wintypes

# --------------------------------------------------------------------------
# Форматирование размера
# --------------------------------------------------------------------------

SIZE_UNITS = ("Б", "КБ", "МБ", "ГБ", "ТБ", "ПБ", "ЭБ")


def human_size(num: float) -> str:
    """Переводит число байт в читаемый вид: 1536 -> '1.5 КБ'."""
    try:
        num = float(num)
    except (TypeError, ValueError):
        return "—"
    if num < 0:
        return "—"
    for unit in SIZE_UNITS:
        if abs(num) < 1024.0 or unit == SIZE_UNITS[-1]:
            if unit == "Б":
                return f"{int(num)} Б"
            return f"{num:.1f} {unit}"
        num /= 1024.0
    return f"{num:.1f} ЭБ"


# --------------------------------------------------------------------------
# Подсчёт размера каталога / файла (Оптимизирован через os.scandir)
# --------------------------------------------------------------------------

def dir_size(path: str) -> tuple[int, int]:
    """Возвращает (суммарный размер в байтах, количество файлов) для файла или дерева."""
    if path is None:
        return 0, 0
    if os.path.isfile(path):
        try:
            return os.path.getsize(path), 1
        except OSError:
            return 0, 0
            
    total = 0
    count = 0
    
    # Итеративный обход через эффективный os.scandir вместо os.walk
    stack = [path]
    while stack:
        current_dir = stack.pop()
        try:
            with os.scandir(current_dir) as entries:
                for entry in entries:
                    try:
                        if entry.is_file(follow_symlinks=False):
                            total += entry.stat(follow_symlinks=False).st_size
                            count += 1
                        elif entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                    except OSError:
                        continue
        except OSError:
            continue
            
    return total, count


def dir_size_limited(path: str, limit: int) -> int:
    """Быстрый подсчёт размера дерева, прерывающийся после достижения limit байт."""
    if os.path.isfile(path):
        try:
            return os.path.getsize(path)
        except OSError:
            return 0
            
    total = 0
    stack = [path]
    while stack:
        current_dir = stack.pop()
        try:
            with os.scandir(current_dir) as entries:
                for entry in entries:
                    try:
                        if entry.is_file(follow_symlinks=False):
                            total += entry.stat(follow_symlinks=False).st_size
                        elif entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                    except OSError:
                        continue
                        
                    if total >= limit:
                        return total
        except OSError:
            continue
    return total


def collect_files(root: str, progress_cb=None, cancel=None) -> list[str]:
    """Возвращает список всех файлов под root (использует стек)."""
    out: list[str] = []
    stack = [root]
    
    while stack:
        if cancel is not None and cancel.is_set():
            break
        current_dir = stack.pop()
        try:
            with os.scandir(current_dir) as entries:
                for entry in entries:
                    if cancel is not None and cancel.is_set():
                        return out
                    try:
                        if entry.is_file(follow_symlinks=False):
                            out.append(entry.path)
                        elif entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                    except OSError:
                        continue
            if progress_cb:
                progress_cb(len(out), current_dir)
        except OSError:
            continue
            
    return out


# --------------------------------------------------------------------------
# Работа с корзиной Windows через Shell API
# --------------------------------------------------------------------------

# ИСПРАВЛЕНО: Добавлен _pack_ = 1 для корректного выравнивания структуры в x64 системах
class _SHFILEOPSTRUCTW(ctypes.Structure):
    _pack_ = 1  
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("wFunc", ctypes.c_uint),
        ("pFrom", wintypes.LPCWSTR),
        ("pTo", wintypes.LPCWSTR),
        ("fFlags", ctypes.c_ushort),
        ("fAnyOperationsAborted", wintypes.BOOL),
        ("hNameMappings", ctypes.c_void_p),
        ("lpszProgressTitle", wintypes.LPCWSTR),
    ]


_FO_DELETE = 0x0003
_FOF_ALLOWUNDO = 0x0040        # разрешает перенос в корзину
_FOF_NOCONFIRMATION = 0x0010   # без диалогов подтверждения
_FOF_NOERRORUI = 0x0400        # не показывать окна ошибок
_FOF_SILENT = 0x0004           # без прогресс-бара


def _shell_operation(src_paths: list[str], to_recycle: bool) -> list[str]:
    """Выполняет операцию удаления через Shell API."""
    errors: list[str] = []
    BATCH_LIMIT = 24_000

    batch: list[str] = []
    batch_len = 0
    for path in src_paths:
        # Нормализуем путь сразу
        normalized_path = os.path.abspath(path).rstrip("\\/")
        item_len = len(normalized_path) + 1  # учитываем один нуль-терминатор

        if batch and batch_len + item_len + 1 > BATCH_LIMIT:  # +1 для финального нуля
            errors.extend(_run_batch(batch, to_recycle))
            batch, batch_len = [], 0
            
        batch.append(normalized_path)
        batch_len += item_len

    if batch:
        errors.extend(_run_batch(batch, to_recycle))
    return errors


def _run_batch(paths: list[str], to_recycle: bool) -> list[str]:
    if not paths:
        return []
        
    # ИСПРАВЛЕНО: Строим корректный double-null буфер без дублирования rstrip
    joined = "\x00".join(paths) + "\x00\x00"
    buf = ctypes.create_unicode_buffer(joined)
    
    op = _SHFILEOPSTRUCTW()
    op.hwnd = None
    op.wFunc = _FO_DELETE
    op.pFrom = ctypes.cast(buf, wintypes.LPCWSTR)
    op.pTo = None
    
    if to_recycle:
        op.fFlags = _FOF_ALLOWUNDO | _FOF_NOCONFIRMATION | _FOF_NOERRORUI | _FOF_SILENT
    else:
        op.fFlags = _FOF_NOCONFIRMATION | _FOF_NOERRORUI | _FOF_SILENT

    result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if result != 0 or op.fAnyOperationsAborted:
        return list(paths)
    return []


def move_to_recycle(paths: list[str]) -> list[str]:
    """Перемещает пути в корзину Windows. Возвращает список неудачных путей."""
    existing = [p for p in paths if os.path.exists(p)]
    return _shell_operation(existing, to_recycle=True)


def delete_permanent(paths: list[str]) -> list[str]:
    """Безвозвратно удаляет пути (файлы и деревья). Возвращает список неудачных путей."""
    errors: list[str] = []
    for p in paths:
        try:
            if os.path.isdir(p) and not os.path.islink(p):
                shutil.rmtree(p, onerror=_rmtree_onerror)
            else:
                _force_remove_file(p)
        except OSError:
            errors.append(p)
    return errors


def _force_remove_file(path: str) -> None:
    try:
        os.chmod(path, 0o666)
        os.remove(path)
    except OSError:
        raise


def _rmtree_onerror(func, path, exc_info):
    try:
        os.chmod(path, 0o666)
        func(path)
    except OSError:
        pass


def clear_readonly(path: str) -> None:
    try:
        os.chmod(path, 0o666)
    except OSError:
        pass


def recycle_bin_size() -> int:
    """Refactored SHQUERYRBINFO."""
    class _SHQUERYRBINFO(ctypes.Structure):
        _pack_ = 1
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("i64Size", ctypes.c_int64),
            ("i64NumItems", ctypes.c_int64),
        ]

    info = _SHQUERYRBINFO()
    info.cbSize = ctypes.sizeof(_SHQUERYRBINFO)
    res = ctypes.windll.shell32.SHQueryRecycleBinW(None, ctypes.byref(info))
    if res == 0:
        return max(info.i64Size, 0)
    return 0


def empty_recycle_bin() -> bool:
    SHERB_NOCONFIRMATION = 0x00000001
    SHERB_NOPROGRESSUI = 0x00000002
    SHERB_NOSOUND = 0x00000004
    flags = SHERB_NOCONFIRMATION | SHERB_NOPROGRESSUI | SHERB_NOSOUND
    res = ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, flags)
    return res == 0


def list_drives() -> list[str]:
    drives = []
    try:
        import string as _string
        for letter in _string.ascii_uppercase:
            root = f"{letter}:\\"
            if os.path.isdir(root): # Более строгая проверка диска
                drives.append(root)
    except Exception:
        pass
    return drives


def scan_path_items(root: str, progress_cb=None, cancel=None) -> list[dict]:
    """Возвращает список записей {path, size, files} для верхнего уровня root (Оптимизирована)."""
    items: list[dict] = []
    if not os.path.isdir(root):
        return items
        
    try:
        # Перешли на os.scandir верхнего уровня для мгновенного отклика
        with os.scandir(root) as entries:
            entries_list = sorted(list(entries), key=lambda e: e.name)
    except OSError:
        return items
        
    total = len(entries_list)
    for i, entry in enumerate(entries_list, start=1):
        if cancel is not None and cancel.is_set():
            break
            
        try:
            if entry.is_symlink():
                continue
            if entry.is_file(follow_symlinks=False):
                size = entry.stat(follow_symlinks=False).st_size
                items.append({"path": entry.path, "size": size, "files": 1})
            elif entry.is_dir(follow_symlinks=False):
                size, cnt = dir_size(entry.path)
                items.append({"path": entry.path, "size": size, "files": cnt})
        except OSError:
            continue  # Переходит к следующему entry, игнорируя строки ниже
            
        # Прогресс обновляется для каждого успешно обработанного элемента
        if progress_cb:
            progress_cb(i, total, entry.path)
            
    # Возвращаем весь собранный список строго после того, как цикл полностью завершился
    return items
