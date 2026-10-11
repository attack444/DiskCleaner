"""Бэкенд умного деинсталлятора: чтение списка программ Windows,
создание бэкапов реестра и зачистка «хвостов» после удаления."""

from __future__ import annotations

import logging
import os
import re
import subprocess
import winreg
from dataclasses import dataclass


@dataclass
class InstalledApp:
    display_name: str
    uninstall_string: str
    install_location: str
    publisher: str
    version: str
    registry_key_path: str
    registry_root: int


# Компоненты пути, которые НИКОГДА не являются «хвостами» софта
_PROTECTED_FOLDERS = {
    "microsoft",
    "windows",
    "nvidia",
    "amd",
    "intel",
    "realtek",
    "google",
    "mozilla",
    "common files",
    "internet explorer",
    "windowsapps",
}


def get_installed_apps() -> list[InstalledApp]:
    apps: list[InstalledApp] = []

    registry_targets = [
        (
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
        ),
        (
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Wow6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
        ),
        (
            winreg.HKEY_CURRENT_USER,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
        ),
    ]

    seen_names: set[str] = set()

    for root, base_path in registry_targets:
        try:
            with winreg.OpenKey(root, base_path, 0, winreg.KEY_READ) as base_key:
                info = winreg.QueryInfoKey(base_key)
                for i in range(info[0]):
                    try:
                        sub_key_name = winreg.EnumKey(base_key, i)
                        full_sub_path = f"{base_path}\\{sub_key_name}"
                        with winreg.OpenKey(
                            root, full_sub_path, 0, winreg.KEY_READ
                        ) as app_key:
                            try:
                                display_name = winreg.QueryValueEx(
                                    app_key, "DisplayName"
                                )[0]
                                uninstall_string = winreg.QueryValueEx(
                                    app_key, "UninstallString"
                                )[0]
                            except OSError:
                                continue

                            if (
                                not display_name
                                or not uninstall_string
                                or sub_key_name.startswith("KB")
                            ):
                                continue

                            try:
                                install_loc = winreg.QueryValueEx(
                                    app_key, "InstallLocation"
                                )[0]
                            except OSError:
                                install_loc = ""
                            try:
                                publisher = winreg.QueryValueEx(app_key, "Publisher")[0]
                            except OSError:
                                publisher = ""
                            try:
                                version = winreg.QueryValueEx(
                                    app_key, "DisplayVersion"
                                )[0]
                            except OSError:
                                version = ""

                            if display_name in seen_names:
                                continue
                            seen_names.add(display_name)

                            apps.append(
                                InstalledApp(
                                    display_name=display_name,
                                    uninstall_string=uninstall_string,
                                    install_location=install_loc,
                                    publisher=publisher,
                                    version=version,
                                    registry_key_path=full_sub_path,
                                    registry_root=root,
                                )
                            )
                    except OSError:
                        continue
        except OSError:
            continue

    return sorted(apps, key=lambda a: a.display_name.lower())


def create_registry_backup(app: InstalledApp, backup_dir: str) -> str | None:
    """Резервная копия ветки реестра приложения через reg.exe."""
    os.makedirs(backup_dir, exist_ok=True)
    clean_name = "".join(
        c for c in app.display_name if c.isalnum() or c in (" ", "_", "-")
    ).strip()
    backup_file = os.path.join(backup_dir, f"backup_{clean_name}.reg")

    root_str = "HKLM" if app.registry_root == winreg.HKEY_LOCAL_MACHINE else "HKCU"
    cmd = f'reg export "{root_str}\\{app.registry_key_path}" "{backup_file}" /y'

    try:
        res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        if res.returncode == 0:
            logging.info(
                f"[Бэкап] Создана резервная копия реестра для {app.display_name}"
            )
            return backup_file
    except Exception as e:
        logging.error(
            f"Не удалось сделать бэкап реестра приложения {app.display_name}: {e}"
        )
    return None


# ----------------------------------------------------------------------
# Матчинг имён папок с ключевыми словами программы
# ----------------------------------------------------------------------

_WORD_BOUNDARY = re.compile(r"[\s\-_\.\(\)\[\]]")


def _keyword_matches(entry_low: str, keywords: list[str]) -> bool:
    """Начало строки ИЛИ отдельное слово. Отсекает случайные совпадения подстрок."""
    if not entry_low:
        return False
    for kw in keywords:
        kw = kw.strip()
        if len(kw) < 3:
            continue
        if entry_low.startswith(kw):
            return True
        if re.search(
            rf"(?:^|[\s\-_\.\(\)\[\]]){re.escape(kw)}(?:$|[\s\-_\.\(\)\[\]])",
            entry_low,
        ):
            return True
    return False


def find_app_leftovers(app: InstalledApp) -> list[str]:
    """Возвращает папки-«хвосты» программы в Program Files / AppData / ProgramData."""
    leftovers: list[str] = []
    keywords: set[str] = set()

    name_low = app.display_name.lower().strip()
    if name_low:
        keywords.add(name_low)

    # Короткое имя без разрядности и версии
    short = re.sub(r"\b(64|32)[- ]?bit\b|\bx64\b|\bx86\b", "", name_low).strip()
    if len(short) >= 3:
        keywords.add(short)

    # Первое слово издателя (Adobe, IObit, ...)
    if app.publisher:
        pub_first = app.publisher.lower().split()[0] if app.publisher.split() else ""
        if len(pub_first) >= 4 and pub_first not in _PROTECTED_FOLDERS:
            keywords.add(pub_first)

    keywords_list = [k for k in keywords if k]

    search_roots = [
        os.environ.get("LOCALAPPDATA", ""),
        os.environ.get("APPDATA", ""),
        os.environ.get("ProgramFiles", ""),
        os.environ.get("ProgramFiles(x86)", ""),
        r"C:\ProgramData",
    ]

    for root in search_roots:
        if not root or not os.path.isdir(root):
            continue
        try:
            with os.scandir(root) as entries:
                for entry in entries:
                    try:
                        if not entry.is_dir(follow_symlinks=False):
                            continue
                        entry_low = entry.name.lower()

                        # Защита от системных папок
                        if entry_low in _PROTECTED_FOLDERS:
                            continue

                        if _keyword_matches(entry_low, keywords_list):
                            leftovers.append(entry.path)
                    except OSError:
                        continue
        except OSError:
            continue

    if app.install_location and os.path.isdir(app.install_location):
        if app.install_location not in leftovers:
            leftovers.append(app.install_location)

    return leftovers
