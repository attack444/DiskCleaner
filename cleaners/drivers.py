"""Работа с драйверами и устройствами Windows.

Использует системные утилиты (не требует сторонних пакетов):
* `Get-PnpDevice` (PowerShell) — список устройств и их статус (быстро);
* `pnputil /enum-drivers` — список установленных пакетов драйверов (OEM-пакетов);
* Windows Update (`UsoClient`, `wuauclt`) и открытие настроек WU — для обновления.
"""
from __future__ import annotations

import os
import re
import json
import subprocess


# --------------------------------------------------------------------------
# Запуск системных команд
# --------------------------------------------------------------------------

def _decode(b: bytes) -> str:
    """Декодирует вывод консоли, устойчиво к разным кодовым страницам."""
    if not b:
        return ""
    try:
        return b.decode("utf-8")
    except (UnicodeDecodeError, LookupError):
        pass

    def _score(s: str) -> int:
        good = 0
        for ch in s:
            o = ord(ch)
            if ch.isprintable() and not (0x2500 <= o <= 0x257F):
                good += 1
        return good

    best, best_score = "", -1
    # Добавили utf-16-le для редких случаев специфического вывода Windows
    for enc in ("cp1251", "cp866", "utf-16-le"):
        try:
            cand = b.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
        sc = _score(cand)
        if sc > best_score:
            best, best_score = cand, sc
    if best:
        return best
    return b.decode("utf-8", errors="replace")


def _run(cmd: list[str], timeout: int = 60) -> tuple[int, str]:
    """Безопасный запуск консольных утилит Windows."""
    try:
        proc = subprocess.run(
            cmd, capture_output=True, timeout=timeout, shell=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return proc.returncode, _decode(proc.stdout) + _decode(proc.stderr)
    except subprocess.TimeoutExpired:
        return -1, f"Ошибка: Превышен таймаут ожидания команды {cmd[0]} ({timeout} сек)."
    except Exception as exc:
        return -1, f"Ошибка запуска: {exc}"


def _run_ps(script: str, timeout: int = 90) -> tuple[int, str]:
    """Запускает PowerShell со скриптом; stdout принудительно в UTF-8."""
    try:
        # Добавлены флаги -NoLogo и -NonInteractive для ускорения инициализации
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NoLogo", "-NonInteractive", 
             "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True, timeout=timeout, shell=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        out = _decode(proc.stdout)
        err = _decode(proc.stderr)
        return proc.returncode, (out + ("\n" + err if err.strip() else ""))
    except subprocess.TimeoutExpired:
        return -1, f"Ошибка: Превышен таймаут ожидания PowerShell ({timeout} сек)."
    except Exception as exc:
        return -1, f"Ошибка запуска PowerShell: {exc}"


# --------------------------------------------------------------------------
# Список устройств (PnP)
# --------------------------------------------------------------------------

def get_pnp_devices(progress_cb=None, cancel=None) -> list[dict]:
    """Возвращает устройства: {instance_id, name, class, status, problem}."""
    devices: list[dict] = []
    # ИСПРАВЛЕНО: Убран флаг -PresentOnly, чтобы видеть неинициализированное железо с ошибками
    script = r"""
\(OutputEncoding = [Console]::OutputEncoding = [Text.Encoding]::UTF8\)devs = Get-PnpDevice -ErrorAction SilentlyContinue
foreach (d in devs) {
    friendly = null
    if (\$d.FriendlyName) { friendly = d.FriendlyName }
    elseif (\$d.Name) { friendly = d.Name }
    if (-not \$friendly) { friendly = d.InstanceId }
    [PSCustomObject]@{
        Id = \$d.InstanceId
        Name = \$friendly
        Class = \$d.Class
        Status = \$d.Status
        Problem = \$d.Problem
    } | ConvertTo-Json -Compress
}
"""
    code, text = _run_ps(script, timeout=60)
    if code != 0 and not text.strip():
        return devices
        
    lines = text.splitlines()
    for line in lines:
        # ИСПРАВЛЕНО: Добавлен прерывание по cancel для исключения "зависания" UI
        if cancel is not None and cancel.is_set():
            return []
            
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
            devices.append({
                "instance_id": obj.get("Id") or "",
                "name": obj.get("Name") or "(без имени)",
                "class": obj.get("Class") or "",
                "status": obj.get("Status") or "",
                "problem": obj.get("Problem") or "",
            })
        except Exception:
            continue
            
        if progress_cb and len(devices) % 10 == 0:
            progress_cb(len(devices), "Обработка устройств…")
            
    return devices


def device_status_summary(devices: list[dict]) -> dict:
    """Сводка: проблемные и отсутствующие устройства."""
    summary = {"total": len(devices), "ok": 0, "problems": [], "missing": []}
    for d in devices:
        st = (d.get("status") or "").lower()
        prob = str(d.get("problem") or "").strip()
        if "error" in st or "unknown" in st or prob not in ("", "CM_PROB_NONE", "0", "0"):
            if prob not in ("", "CM_PROB_NONE", "0", "0"):
                summary["problems"].append(d)
            else:
                summary["missing"].append(d)
        else:
            summary["ok"] += 1
    return summary


# --------------------------------------------------------------------------
# Список пакетов драйверов (pnputil)
# --------------------------------------------------------------------------

def _classify_key(label: str) -> str:
    key = re.sub(r"\s+", "", label).lower()
    if not key:
        return ""
    if key.startswith("guid"):
        return ""
    if key.startswith("опубликован") or key.startswith("published"):
        return "published"
    if key.startswith("исходн") or "пакет" in key or "оригинал" in key \
            or key.startswith("original"):
        return "original"
    if "поставщик" in key or "provider" in key:
        return "provider"
    if "класс" in key or "класа" in key or key in ("class", "classname"):
        return "class"
    if "версия" in key or "version" in key:
        return "version"
    if "дата" in key or "date" in key:
        return "date"
    return ""


def _looks_like_pub(line_value: str) -> bool:
    return bool(re.search(r"\.inf\s*\$", line_value.strip(), re.IGNORECASE))


def get_driver_packages(progress_cb=None, cancel=None) -> list[dict]:
    """Возвращает установленные пакеты драйверов из pnputil."""
    code, text = _run(["pnputil", "/enum-drivers"], timeout=60)
    if code != 0:
        return []

    drivers: list[dict] = []
    cur: dict | None = None

    lines = text.splitlines()
    for raw in lines:
        # ИСПРАВЛЕНО: Добавлена проверка cancel во время парсинга длинного списка инф-пакетов
        if cancel is not None and cancel.is_set():
            return []

        line = raw.rstrip()
        if not line.strip() or ":" not in line:
            continue
        label, _, value = line.partition(":")
        label = label.strip()
        value = value.strip()
        field = _classify_key(label)

        if field == "published" and _looks_like_pub(value):
            if cur:
                drivers.append(cur)
                if progress_cb:
                    progress_cb(len(drivers), f"Обработка: {cur.get('name', '')}")
            cur = {"name": value, "original": "", "provider": "",
                   "class": "", "version": "", "date": ""}
            continue

        if cur is None:
            if _looks_like_pub(value) and field in ("original", ""):
                cur = {"name": value, "original": value, "provider": "",
                       "class": "", "version": "", "date": ""}
            continue

        if field == "original":
            cur["original"] = value
        elif field == "provider":
            cur["provider"] = value
        elif field == "class":
            cur["class"] = value
        elif field == "version":
            m = re.match(r"^(\d{1,2}[./]\d{1,2}[./]\d{2,4})\s+(.+)\$", value)
            if m:
                cur["date"] = m.group(1)
                cur["version"] = m.group(2)
            else:
                cur["version"] = value
        elif field == "date":
            cur["date"] = value.split()[0] if value.split() else value

    if cur:
        drivers.append(cur)
    return drivers


# --------------------------------------------------------------------------
# Проверка и обновление через Windows Update
# --------------------------------------------------------------------------

def check_windows_update(progress_cb=None, cancel=None) -> dict:
    """Запускает проверку обновлений (в т.ч. драйверов) в фоне."""
    if progress_cb:
        progress_cb("Запуск проверки Центра обновления Windows…")

    code, out = _run(["UsoClient", "StartScan"], timeout=60)
    if code != 0:
        code, out = _run(["wuauclt", "/detectnow"], timeout=60)

    result = {
        "started": code == 0 or "success" in out.lower(),
        "output": out.strip()[:2000],
        "code": code,
    }
    if progress_cb:
        progress_cb("Проверка запущена. Обновления будут найдены в фоне.")
    return result


def open_windows_update_settings() -> bool:
    try:
        os.startfile("ms-settings:windowsupdate")
        return True
    except Exception:
        return False


def open_optional_updates() -> bool:
    try:
        os.startfile("ms-settings:windowsupdate-optionalupdates")
        return True
    except Exception:
        return False
