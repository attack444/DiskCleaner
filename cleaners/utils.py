"""Общие утилиты: форматирование размеров, подсчёт, удаление в корзину Windows.

Использует исключительно стандартную библиотеку Python + Win32 API через ctypes.
"""

from __future__ import annotations

import ctypes
import logging
import os
import shutil
import tempfile
from ctypes import wintypes


def setup_app_logging():
    """Настраивает автоматическую запись логов приложения во временную папку."""
    log_dir = os.path.join(os.environ.get("TEMP", tempfile.gettempdir()), "DiskCleaner")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "app_running.log")

    logging.basicConfig(
        filename=log_file,
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        encoding="utf-8",
        filemode="w",
    )
    logging.info("=== Движок DiskCleaner запущен успешно ===")
    return log_file


# --------------------------------------------------------------------------
# Форматирование размера
# --------------------------------------------------------------------------

SIZE_UNITS = ("Б", "КБ", "МБ", "ГБ", "ТБ", "ПБ", "ЭБ")


def human_size(num: float) -> str:
    """Переводит число байт в читаемый вид: 1536 -> '1.5 КБ'."""
    try:
        num = float(num)
    except TypeError, ValueError:
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
# Подсчёт размера каталога / файла (через os.scandir)
# --------------------------------------------------------------------------


def dir_size(path: str) -> tuple[int, int]:
    """Возвращает (суммарный размер в байтах, количество файлов)."""
    if path is None:
        return 0, 0
    if os.path.isfile(path):
        try:
            return os.path.getsize(path), 1
        except OSError:
            return 0, 0

    total = 0
    count = 0
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


class _SHFILEOPSTRUCTW(ctypes.Structure):
    """SHFILEOPSTRUCTW из shellapi.h.

    ВАЖНО: без _pack_ = 1 — структура выравнивается естественно,
    как ожидает Windows. С _pack_=1 смещения полей не совпадают,
    и ShellExecute ловит access violation.
    """

    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("wFunc", ctypes.c_uint),
        ("pFrom", wintypes.LPCWSTR),
        ("pTo", wintypes.LPCWSTR),
        ("fFlags", ctypes.c_ushort),  # FILEOP_FLAGS = WORD
        ("fAnyOperationsAborted", wintypes.BOOL),
        ("hNameMappings", ctypes.c_void_p),
        ("lpszProgressTitle", wintypes.LPCWSTR),
    ]


_FO_DELETE = 0x0003
_FOF_ALLOWUNDO = 0x0040
_FOF_NOCONFIRMATION = 0x0010
_FOF_NOERRORUI = 0x0400
_FOF_SILENT = 0x0004
_FOF_NOCONFIRMMKDIR = 0x0200

# SHFileOperationW не любит очень длинные списки, шлём пачками
_BATCH = 200


def _shfileop(paths: list[str], recycle: bool) -> list[str]:
    """Низкоуровневый вызов SHFileOperationW.
    Возвращает пути, которые ОСТАЛИСЬ на диске (не удалось удалить/переместить).
    """
    if not paths:
        return []

    cleaned: list[str] = []
    for p in paths:
        ap = os.path.abspath(p)
        if len(ap) > 3 and ap.endswith("\\"):
            ap = ap.rstrip("\\")
        cleaned.append(ap)

    joined = "\x00".join(cleaned) + "\x00\x00"
    buf = ctypes.create_unicode_buffer(joined)

    op = _SHFILEOPSTRUCTW()
    op.hwnd = None
    op.wFunc = _FO_DELETE
    op.pFrom = ctypes.cast(buf, wintypes.LPCWSTR)
    op.pTo = None

    flags = _FOF_NOCONFIRMATION | _FOF_NOERRORUI | _FOF_SILENT | _FOF_NOCONFIRMMKDIR
    if recycle:
        flags |= _FOF_ALLOWUNDO
    op.fFlags = flags

    try:
        ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    except Exception as e:
        logging.error(f"SHFileOperationW упал: {e}")
        return list(paths)

    # Факт-чек: что реально осталось на диске
    return [p for p in paths if os.path.exists(p) or os.path.islink(p)]


def _rmtree_onerror(func, path, exc_info):
    try:
        os.chmod(path, 0o666)
        func(path)
    except OSError:
        pass


def _fallback_delete(path: str) -> bool:
    """Резервное удаление файловым API (для сетевых шар, где Shell API молчит)."""
    try:
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path, onerror=_rmtree_onerror)
        else:
            try:
                os.chmod(path, 0o666)
            except OSError:
                pass
            os.remove(path)
    except OSError as e:
        logging.error(f"[fallback] не удалось удалить {path}: {e}")
        return False
    return not (os.path.exists(path) or os.path.islink(path))


def move_to_recycle(paths: list[str]) -> list[str]:
    """Отправляет объекты в Корзину Windows через Shell API.

    Возвращает список путей, которые НЕ удалось переместить в Корзину.
    Каждый успех логируется отдельной строкой `RECYCLED: <path>` —
    UI может показать «можно восстановить из Корзины».
    """
    if not paths:
        return []

    normalized: list[str] = []
    for p in paths:
        if p and isinstance(p, str):
            ap = os.path.abspath(p)
            if os.path.exists(ap) or os.path.islink(ap):
                normalized.append(ap)
    if not normalized:
        return []

    failed: list[str] = []
    for i in range(0, len(normalized), _BATCH):
        chunk = normalized[i : i + _BATCH]
        still_on_disk = set(_shfileop(chunk, recycle=True))
        for p in chunk:
            if p in still_on_disk:
                failed.append(p)
                logging.error(f"Не удалось поместить в Корзину: {p}")
            else:
                logging.info(f"RECYCLED: {p}")
    return failed


def delete_permanent(paths: list[str]) -> list[str]:
    """Безвозвратное удаление. Shell API → fallback на файловый API."""
    if not paths:
        return []

    normalized: list[str] = []
    for p in paths:
        if p and isinstance(p, str):
            ap = os.path.abspath(p)
            if os.path.exists(ap) or os.path.islink(ap):
                normalized.append(ap)
    if not normalized:
        return []

    failed: list[str] = []
    for i in range(0, len(normalized), _BATCH):
        chunk = normalized[i : i + _BATCH]
        still_on_disk = _shfileop(chunk, recycle=False)
        for p in still_on_disk:
            if _fallback_delete(p):
                logging.info(f"DELETED: {p}")
            else:
                failed.append(p)
    return failed


def request_delete_on_reboot(path: str) -> bool:
    """Просит Windows удалить заблокированный файл при следующей перезагрузке."""
    if not path or not os.path.exists(path):
        return False

    MOVEFILE_DELAY_UNTIL_REBOOT = 0x00000004
    try:
        normalized_path = os.path.abspath(path)
        ok = ctypes.windll.kernel32.MoveFileExW(
            normalized_path,
            None,
            MOVEFILE_DELAY_UNTIL_REBOOT,
        )
        if ok:
            logging.info(f"[PENDING DELETE] {normalized_path}")
            return True
    except Exception as e:
        logging.error(f"Не удалось запланировать удаление файла {path}: {e}")
    return False


def clear_readonly(path: str) -> None:
    try:
        os.chmod(path, 0o666)
    except OSError:
        pass


def recycle_bin_size() -> int:
    class _SHQUERYRBINFO(ctypes.Structure):
        _pack_ = 1
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("i64Size", ctypes.c_int64),
            ("i64NumItems", ctypes.c_int64),
        ]

    info = _SHQUERYRBINFO()
    info.cbSize = ctypes.sizeof(_SHQUERYRBINFO)
    if ctypes.windll.shell32.SHQueryRecycleBinW(None, ctypes.byref(info)) == 0:
        return max(info.i64Size, 0)
    return 0


def empty_recycle_bin() -> bool:
    """Очищает корзину Windows без вывода диалоговых окон и звуков."""
    SHERB_NOCONFIRMATION = 0x00000001
    SHERB_NOPROGRESSUI = 0x00000002
    SHERB_NOSOUND = 0x00000004
    flags = SHERB_NOCONFIRMATION | SHERB_NOPROGRESSUI | SHERB_NOSOUND
    return ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, flags) == 0


def list_drives() -> list[str]:
    drives = []
    try:
        import string as _string

        for letter in _string.ascii_uppercase:
            root = f"{letter}:\\"
            if os.path.isdir(root):
                drives.append(root)
    except Exception:
        pass
    return drives


def scan_path_items(root: str, progress_cb=None, cancel=None) -> list[dict]:
    items: list[dict] = []
    if not os.path.isdir(root):
        return items
    try:
        with os.scandir(root) as it:
            entries_list = sorted(it, key=lambda e: e.name)
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
            continue
        if progress_cb:
            progress_cb(i, total, entry.path)

    return items
