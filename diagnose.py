#!/usr/bin/env python3
"""Диагностика DiskCleaner. Запуск: uv run python diagnose.py

Проверяет окружение, PowerShell, модули проекта, сеть, smart-правила.
Печатает в stdout — можно копировать в багрепорт.
"""

from __future__ import annotations

import ctypes
import os
import platform
import subprocess
import sys
import time
import urllib.request


# ------------------------------------------------------------------
# Цветной вывод
# ------------------------------------------------------------------

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
DIM = "\033[2m"
RESET = "\033[0m"


def ok(msg: str):
    print(f"{GREEN}  ✓ {msg}{RESET}")


def bad(msg: str):
    print(f"{RED}  ✗ {msg}{RESET}")


def warn(msg: str):
    print(f"{YELLOW}  ! {msg}{RESET}")


def info(msg: str):
    print(f"{DIM}    {msg}{RESET}")


def section(title: str):
    print(f"\n{CYAN}=== {title} ==={RESET}")


# ------------------------------------------------------------------
# 1. Окружение
# ------------------------------------------------------------------


def check_environment() -> None:
    section("1. Окружение")
    ok(f"Python: {sys.version.split()[0]}")
    ok(f"Платформа: {platform.platform()}")
    ok(f"Рабочий каталог: {os.getcwd()}")
    if hasattr(sys, "_MEIPASS"):
        ok(f"Запущено из PyInstaller: {sys._MEIPASS}")
    else:
        info("Запущено из исходников")

    try:
        is_admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        is_admin = False
    if is_admin:
        ok("Права администратора: есть")
    else:
        warn("Права администратора: НЕТ — часть системных папок будет пропущена")


# ------------------------------------------------------------------
# 2. PowerShell
# ------------------------------------------------------------------


def _run_ps(script: str, timeout: int = 30) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NoLogo",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ],
            capture_output=True,
            timeout=timeout,
            shell=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        out = proc.stdout.decode("utf-8", errors="replace")
        err = proc.stderr.decode("utf-8", errors="replace")
        return proc.returncode, (out + ("\n" + err if err.strip() else ""))
    except FileNotFoundError:
        return -1, "powershell.exe не найден"
    except subprocess.TimeoutExpired:
        return -1, f"timeout {timeout}s"
    except Exception as e:
        return -1, f"{type(e).__name__}: {e}"


def check_powershell() -> bool:
    section("2. PowerShell")
    code, out = _run_ps("Write-Output 'ok'", timeout=15)
    if code == 0 and "ok" in out:
        ok("PowerShell доступен")
        return True
    bad(f"PowerShell не отвечает. Код={code}, вывод={out[:200]!r}")
    return False


# ------------------------------------------------------------------
# 3. Модули проекта
# ------------------------------------------------------------------

_MODULES = (
    "cleaners",
    "cleaners.utils",
    "cleaners.safety",
    "cleaners.report",
    "cleaners.smart_rules",
    "cleaners.disk_scan",
    "cleaners.duplicate_finder",
    "cleaners.system_cleaner",
    "cleaners.full_check",
    "cleaners.registry_cleaner",
    "cleaners.uninstaller",
    "ui.app",
    "ui.common",
    "ui.fullcheck_tab",
    "ui.uninstaller_tab",
)


def check_modules() -> None:
    section("3. Модули проекта")
    for name in _MODULES:
        try:
            __import__(name)
            ok(f"импорт {name}")
        except Exception as e:
            bad(f"{name}: {type(e).__name__}: {e}")


# ------------------------------------------------------------------
# 4. Сеть
# ------------------------------------------------------------------


def check_network() -> None:
    section("4. Сеть")
    urls = [
        ("NVIDIA", "https://www.nvidia.com/"),
        ("Bing", "https://www.bing.com/"),  # Microsoft-owned, но без 403-защиты
        ("GitHub", "https://api.github.com/"),  # стабильный публичный endpoint
    ]
    for name, url in urls:
        t0 = time.time()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=8) as r:
                code = r.status
            dt = time.time() - t0
            ok(f"{name}: HTTP {code} за {dt:.1f}s")
        except Exception as e:
            bad(f"{name}: {type(e).__name__}: {e}")


# ------------------------------------------------------------------
# 5. SmartRules
# ------------------------------------------------------------------


def check_smart_rules() -> None:
    section("5. SmartRules")
    try:
        from cleaners.smart_rules import judge_file
    except Exception as e:
        bad(f"Импорт smart_rules: {type(e).__name__}: {e}")
        return

    cases = [
        (r"C:\Windows\System32\kernel32.dll", "protected"),
        (r"C:\Program Files\App\app.exe", "protected"),
        (
            r"D:\Steam\steamapps\common\PUBG\TslGame\Content\Paks\pakchunk0-WindowsNoEditor.pak",
            "protected",
        ),
        (r"D:\Git\ucrt64\libexec\git-core\libiconv-2.dll", "protected"),
        (r"C:\Users\Иван\AppData\Local\Temp\foo.tmp", "safe"),
        (r"C:\Users\Иван\Downloads\setup_old.exe", "caution"),
        (r"C:\Users\Иван\Documents\report.docx", "caution"),
        (r"C:\Data\backup.iso", "caution"),
    ]
    passed = 0
    for path, expected in cases:
        try:
            j = judge_file(path, 1024)
            verdict = j.verdict.value
        except Exception as e:
            bad(f"{path} → исключение: {type(e).__name__}: {e}")
            continue
        if verdict == expected:
            ok(f"{expected:<10} ← {path}")
            passed += 1
        else:
            bad(f"Ожидалось {expected}, получено {verdict} для {path}")
    print(f"{DIM}    пройдено: {passed}/{len(cases)}{RESET}")


# ------------------------------------------------------------------
# 6. Быстрый smoke-тест сканера
# ------------------------------------------------------------------


def check_disk_scan() -> None:
    section("6. Быстрый smoke-тест disk_scan")
    try:
        from cleaners.disk_scan import unified_scan
    except Exception as e:
        bad(f"Импорт disk_scan: {type(e).__name__}: {e}")
        return

    import tempfile

    with tempfile.TemporaryDirectory(prefix="dc_smoke_") as td:
        # 2 больших файла, 1 мелкий, 1 пустая папка
        big1 = os.path.join(td, "big1.bin")
        big2 = os.path.join(td, "big2.bin")
        small = os.path.join(td, "small.txt")
        empty_dir = os.path.join(td, "empty")
        os.makedirs(empty_dir, exist_ok=True)
        with open(big1, "wb") as f:
            f.write(b"x" * 5_000_000)
        with open(big2, "wb") as f:
            f.write(b"y" * 8_000_000)
        with open(small, "wb") as f:
            f.write(b"hello")

        try:
            res = unified_scan(td, big_min_bytes=1_000_000, top=10)
        except Exception as e:
            bad(f"unified_scan бросил: {type(e).__name__}: {e}")
            return

        big_count = len(res["big_files"])
        empty_count = len(res["empty_folders"])
        if big_count == 2:
            ok(f"Найдено крупных: {big_count}")
        else:
            bad(f"Ожидалось 2 крупных, получено {big_count}")

        if empty_count == 1:
            ok(f"Найдено пустых папок: {empty_count}")
        else:
            bad(f"Ожидалось 1 пустая папка, получено {empty_count}")

        # Проверка file_filter
        res2 = unified_scan(
            td,
            big_min_bytes=1,
            file_filter=lambda p, s: p.endswith(".bin"),
        )
        filtered = len(res2["big_files"])
        if filtered == 2:
            ok(f"file_filter работает (отобрано: {filtered})")
        else:
            bad(f"file_filter: ожидалось 2, получено {filtered}")


# ------------------------------------------------------------------
# 7. Корзина (Shell API)
# ------------------------------------------------------------------


def check_recycle_bin() -> None:
    section("7. Корзина Windows")
    try:
        from cleaners.utils import recycle_bin_size
    except Exception as e:
        bad(f"Импорт utils: {type(e).__name__}: {e}")
        return
    try:
        size = recycle_bin_size()
        ok(f"recycle_bin_size() = {size} байт")
    except Exception as e:
        bad(f"recycle_bin_size бросил: {type(e).__name__}: {e}")

    # Проверяем, что SHFileOperation не падает с access violation
    import tempfile

    try:
        from cleaners.utils import move_to_recycle

        with tempfile.TemporaryDirectory(prefix="dc_rb_") as td:
            f = os.path.join(td, "junk.tmp")
            with open(f, "wb") as fp:
                fp.write(b"x" * 100)
            bad_list = move_to_recycle([f])
            if not os.path.exists(f) and not bad_list:
                ok("move_to_recycle() → файл ушёл в Корзину")
            elif bad_list:
                warn(f"move_to_recycle() → не удалось: {bad_list}")
            else:
                bad(
                    "move_to_recycle() → файл исчез, но функция вернула пусто (подозрительно)"
                )
    except Exception as e:
        bad(f"move_to_recycle: {type(e).__name__}: {e}")


# ------------------------------------------------------------------
# main
# ------------------------------------------------------------------


def main():
    print(f"{CYAN}{'=' * 60}")
    print(f"  DiskCleaner — диагностика")
    print(f"{'=' * 60}{RESET}")
    print(f"{DIM}Лог диагностики. Скопируй и приложи к багрепорту.{RESET}")

    check_environment()
    if not check_powershell():
        print(
            f"\n{RED}PowerShell не работает — остальные проверки бессмысленны.{RESET}"
        )
        return 1

    check_modules()
    check_network()
    check_smart_rules()
    check_disk_scan()
    check_recycle_bin()

    print(f"\n{GREEN}Диагностика завершена.{RESET}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
