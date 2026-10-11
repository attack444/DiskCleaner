"""Безопасный анализатор и чистильщик устаревших записей Реестра Windows."""

from __future__ import annotations

import logging
import os
import winreg

# Ветка MUICache — кэш дружественных имён запущенных exe
_MUI_PATH = r"Software\Classes\Local Settings\Software\Microsoft\Windows\Shell\MuiCache"
# Ветка App Paths — «быстрый запуск» программ
_APP_PATHS = r"Software\Microsoft\Windows\CurrentVersion\App Paths"

_SKIP_VALUE_NAMES = {"LangID", "Value"}


def _iter_mui_invalid(cancel=None) -> list[dict]:
    invalid: list[dict] = []
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _MUI_PATH, 0, winreg.KEY_READ
        ) as key:
            info = winreg.QueryInfoKey(key)
            for i in range(info[1]):
                if cancel is not None and cancel.is_set():
                    return invalid
                try:
                    name, value, _ = winreg.EnumValue(key, i)
                except OSError:
                    continue
                if not name or name in _SKIP_VALUE_NAMES:
                    continue
                exe_path = name.split(".FriendlyAppName")[0].split(
                    ".ApplicationCompany"
                )[0]
                if exe_path.lower().endswith(".exe") and not os.path.exists(exe_path):
                    invalid.append(
                        {
                            "hive": "HKCU",
                            "key_path": _MUI_PATH,
                            "param_name": name,
                            "display_name": str(value),
                            "dead_path": exe_path,
                        }
                    )
    except OSError as e:
        logging.error(f"Не удалось открыть раздел реестра MuiCache: {e}")
    return invalid


def _iter_app_paths_invalid(cancel=None) -> list[dict]:
    invalid: list[dict] = []
    for hive, hive_name in (
        (winreg.HKEY_CURRENT_USER, "HKCU"),
        (winreg.HKEY_LOCAL_MACHINE, "HKLM"),
    ):
        try:
            with winreg.OpenKey(hive, _APP_PATHS, 0, winreg.KEY_READ) as base:
                info = winreg.QueryInfoKey(base)
                for i in range(info[0]):
                    if cancel is not None and cancel.is_set():
                        return invalid
                    try:
                        sub_name = winreg.EnumKey(base, i)
                    except OSError:
                        continue
                    full = f"{_APP_PATHS}\\{sub_name}"
                    try:
                        with winreg.OpenKey(hive, full, 0, winreg.KEY_READ) as sub:
                            try:
                                target, _ = winreg.QueryValueEx(sub, "")
                            except OSError:
                                target = ""
                            if not target:
                                continue
                            # Иногда значение содержит аргументы через запятую
                            exe = target.split(",")[0].strip().strip('"')
                            if exe and not os.path.exists(exe):
                                invalid.append(
                                    {
                                        "hive": hive_name,
                                        "key_path": full,
                                        "param_name": "Вся ветка",
                                        "display_name": sub_name,
                                        "dead_path": exe,
                                    }
                                )
                    except OSError:
                        continue
        except OSError:
            continue
    return invalid


def scan_invalid_mui_cache(cancel=None, progress_cb=None) -> list[dict]:
    """Возвращает список «мёртвых» записей MuiCache и App Paths."""
    result: list[dict] = []
    if progress_cb:
        progress_cb("Сканирование MuiCache…")
    result.extend(_iter_mui_invalid(cancel=cancel))
    if cancel is not None and cancel.is_set():
        return result
    if progress_cb:
        progress_cb("Сканирование App Paths…")
    result.extend(_iter_app_paths_invalid(cancel=cancel))
    return result


def delete_registry_params(entries: list[dict]) -> int:
    """Удаляет битые параметры/ветки из реестра Windows."""
    deleted_count = 0
    for item in entries:
        hive = (
            winreg.HKEY_CURRENT_USER
            if item.get("hive") == "HKCU"
            else winreg.HKEY_LOCAL_MACHINE
        )
        key_path = item.get("key_path") or ""
        param_name = item.get("param_name") or ""

        try:
            # Случай App Paths: удаляем всю подключевую ветку
            if param_name == "Вся ветка":
                with winreg.OpenKey(
                    hive, key_path.rsplit("\\", 1)[0], 0, winreg.KEY_SET_VALUE
                ) as parent:
                    winreg.DeleteKey(parent, key_path.rsplit("\\", 1)[1])
                    deleted_count += 1
                    logging.info(f"[Реестр] Удалена ветка: {key_path}")
                continue

            # Обычный случай: удаляем именованный параметр
            with winreg.OpenKey(hive, key_path, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, param_name)
                deleted_count += 1
                logging.info(f"[Реестр] Удалён параметр {param_name} из {key_path}")
        except OSError as e:
            logging.error(f"Не удалось удалить запись {key_path}::{param_name} — {e}")
            continue

    return deleted_count
