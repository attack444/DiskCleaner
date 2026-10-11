"""Управление автозагрузкой Windows: реестр Run/RunOnce + Startup-папки."""

from __future__ import annotations

import logging
import os
import re
import winreg
from dataclasses import dataclass
from pathlib import Path


@dataclass
class StartupEntry:
    uid: str  # уникальный идентификатор для UI
    name: str
    command: str
    source: str  # "HKLM\\Run" / "Startup Folder"
    source_label: str  # человекочитаемо
    kind: str  # "registry" | "folder"
    hive: str  # "HKCU" | "HKLM" | ""
    key_path: str  # путь в реестре (для registry)
    value_name: str  # имя значения (для registry)
    file_path: str  # физический путь к файлу (если удалось распарсить)
    enabled: bool
    exists: bool  # файл ещё существует


# ------------------------------------------------------------------
# Реестр: ключи автозагрузки
# ------------------------------------------------------------------

_RUN_KEYS = (
    (
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\Run",
        "HKCU",
        "Пользователь (HKCU)",
    ),
    (
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\RunOnce",
        "HKCU",
        "Пользователь (HKCU, один раз)",
    ),
    (
        winreg.HKEY_LOCAL_MACHINE,
        r"Software\Microsoft\Windows\CurrentVersion\Run",
        "HKLM",
        "Все пользователи (HKLM)",
    ),
    (
        winreg.HKEY_LOCAL_MACHINE,
        r"Software\Microsoft\Windows\CurrentVersion\RunOnce",
        "HKLM",
        "Все пользователи (HKLM, один раз)",
    ),
    (
        winreg.HKEY_LOCAL_MACHINE,
        r"Software\Wow6432Node\Microsoft\Windows\CurrentVersion\Run",
        "HKLM",
        "Все пользователи (HKLM, x86)",
    ),
    (
        winreg.HKEY_LOCAL_MACHINE,
        r"Software\Wow6432Node\Microsoft\Windows\CurrentVersion\RunOnce",
        "HKLM",
        "Все пользователи (HKLM, x86, один раз)",
    ),
)

_DISABLED_PREFIX = "__dc_disabled__"


# ------------------------------------------------------------------
# Разбор командной строки
# ------------------------------------------------------------------

_QUOTED_EXE = re.compile(r'^\s*"([^"]+)"')
_UNQUOTED_EXE = re.compile(
    r"^\s*([^\s]+\.(?:exe|com|bat|cmd|vbs|lnk|ps1))", re.IGNORECASE
)


def _extract_exe(command: str) -> str:
    """Возвращает путь к исполняемому файлу из командной строки."""
    if not command:
        return ""
    command = command.strip()
    m = _QUOTED_EXE.match(command)
    if m:
        return m.group(1)
    m = _UNQUOTED_EXE.match(command)
    if m:
        return m.group(1)
    # Возможно, просто путь без кавычек и расширения
    return command.split()[0] if command else ""


def _expand(path: str) -> str:
    if not path:
        return ""
    return os.path.expandvars(path.replace('"', "").strip())


# ------------------------------------------------------------------
# Сканирование
# ------------------------------------------------------------------


def scan_startup(cancel=None, progress_cb=None) -> list[StartupEntry]:
    entries: list[StartupEntry] = []
    if progress_cb:
        progress_cb("Чтение ключей реестра Run/RunOnce…")

    # --- 1. Реестр
    for hive, path, hive_name, label in _RUN_KEYS:
        if cancel is not None and cancel.is_set():
            return entries
        try:
            with winreg.OpenKey(hive, path, 0, winreg.KEY_READ) as key:
                info = winreg.QueryInfoKey(key)
                for i in range(info[1]):
                    if cancel is not None and cancel.is_set():
                        return entries
                    try:
                        name, value, _ = winreg.EnumValue(key, i)
                    except OSError:
                        continue
                    if not isinstance(value, str):
                        continue

                    enabled = not name.startswith(_DISABLED_PREFIX)
                    display_name = (
                        name[len(_DISABLED_PREFIX) :] if not enabled else name
                    )

                    exe = _expand(_extract_exe(value))
                    exists = bool(exe) and (os.path.isfile(exe) or os.path.isdir(exe))

                    entries.append(
                        StartupEntry(
                            uid=f"reg:{hive_name}:{path}:{name}",
                            name=display_name,
                            command=value,
                            source=f"{hive_name}\\...\\{path.rsplit(chr(92), 1)[-1]}",
                            source_label=label,
                            kind="registry",
                            hive=hive_name,
                            key_path=path,
                            value_name=name,
                            file_path=exe,
                            enabled=enabled,
                            exists=exists,
                        )
                    )
        except FileNotFoundError:
            continue
        except OSError as e:
            logging.debug(f"Не удалось открыть {hive_name}\\{path}: {e}")

    if progress_cb:
        progress_cb(f"Реестр: {len(entries)} записей")

    # --- 2. Папки Startup
    folders = (
        (
            os.path.join(
                os.environ.get("APPDATA", ""),
                r"Microsoft\Windows\Start Menu\Programs\Startup",
            ),
            "Пользователь",
        ),
        (
            os.path.join(
                os.environ.get("ProgramData", ""),
                r"Microsoft\Windows\Start Menu\Programs\Startup",
            ),
            "Все пользователи",
        ),
    )
    for folder, who in folders:
        if cancel is not None and cancel.is_set():
            return entries
        if not folder or not os.path.isdir(folder):
            continue
        # Файлы в подпапке _disabled считаются отключёнными
        for dirpath, dirnames, filenames in os.walk(folder):
            for fname in filenames:
                if cancel is not None and cancel.is_set():
                    return entries
                if fname.lower() in ("desktop.ini",):
                    continue
                full = os.path.join(dirpath, fname)
                rel_to_root = os.path.relpath(full, folder)
                enabled = not rel_to_root.lower().startswith("_disabled")
                entries.append(
                    StartupEntry(
                        uid=f"folder:{full}",
                        name=fname,
                        command=full,
                        source="Startup Folder",
                        source_label=f"Папка Автозагрузки · {who}",
                        kind="folder",
                        hive="",
                        key_path=folder,
                        value_name=full,
                        file_path=full,
                        enabled=enabled,
                        exists=True,
                    )
                )

    if progress_cb:
        progress_cb(f"Всего записей автозагрузки: {len(entries)}")
    return entries


# ------------------------------------------------------------------
# Включить / выключить
# ------------------------------------------------------------------


def _reg_root(hive_name: str) -> int:
    return (
        winreg.HKEY_CURRENT_USER if hive_name == "HKCU" else winreg.HKEY_LOCAL_MACHINE
    )


def set_registry_enabled(entry: StartupEntry, enable: bool) -> tuple[bool, str]:
    """Переименовывает значение с префиксом __dc_disabled__ для отключения."""
    if entry.kind != "registry":
        return False, "Не реестровая запись"
    hive = _reg_root(entry.hive)
    try:
        with winreg.OpenKey(
            hive, entry.key_path, 0, winreg.KEY_READ | winreg.KEY_SET_VALUE
        ) as key:
            # читаем текущее
            try:
                value, vtype = winreg.QueryValueEx(key, entry.value_name)
            except OSError as e:
                return False, f"Не удалось прочитать значение: {e}"

            if enable:
                # убрать префикс
                if not entry.value_name.startswith(_DISABLED_PREFIX):
                    return True, "Уже включено"
                new_name = entry.value_name[len(_DISABLED_PREFIX) :]
            else:
                # добавить префикс
                if entry.value_name.startswith(_DISABLED_PREFIX):
                    return True, "Уже отключено"
                new_name = _DISABLED_PREFIX + entry.value_name

            winreg.SetValueEx(key, new_name, 0, vtype, value)
            winreg.DeleteValue(key, entry.value_name)
        return True, "Переименовано"
    except OSError as e:
        return False, f"{type(e).__name__}: {e}"


def set_folder_enabled(entry: StartupEntry, enable: bool) -> tuple[bool, str]:
    """Перемещает ярлык между Startup и Startup\\_disabled."""
    if entry.kind != "folder":
        return False, "Не файловая запись"
    src = entry.value_name
    folder = entry.key_path
    disabled_dir = os.path.join(folder, "_disabled")
    try:
        if enable:
            # ищем файл в _disabled
            candidate = os.path.join(disabled_dir, os.path.basename(src))
            if os.path.isfile(candidate):
                os.makedirs(folder, exist_ok=True)
                os.replace(candidate, os.path.join(folder, os.path.basename(src)))
                return True, "Включено"
            if os.path.isfile(src):
                return True, "Уже включено"
            return False, "Файл не найден"
        else:
            os.makedirs(disabled_dir, exist_ok=True)
            dst = os.path.join(disabled_dir, os.path.basename(src))
            if os.path.isfile(src):
                os.replace(src, dst)
                return True, "Отключено"
            if os.path.isfile(dst):
                return True, "Уже отключено"
            return False, "Файл не найден"
    except OSError as e:
        return False, f"{type(e).__name__}: {e}"


def set_enabled(entry: StartupEntry, enable: bool) -> tuple[bool, str]:
    if entry.kind == "registry":
        return set_registry_enabled(entry, enable)
    return set_folder_enabled(entry, enable)


def open_file_location(entry: StartupEntry) -> bool:
    """Открывает проводник на файле автозагрузки."""
    path = entry.file_path or ""
    if not path or not os.path.exists(path):
        return False
    try:
        if os.path.isdir(path):
            os.startfile(path)
        else:
            os.startfile(os.path.dirname(path))
        return True
    except Exception:
        return False
