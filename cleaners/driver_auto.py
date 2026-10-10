"""Автоподбор и установка драйверов с официальных источников производителей.

Принцип (по требованию — без сторонних установщиков, без Windows Update).
"""
from __future__ import annotations

import os
import re
import json
import time
import ctypes
from ctypes import wintypes
import tempfile
import subprocess
import urllib.parse
import urllib.request

from . import hardware_scan
from .drivers import _decode, _run_ps

# --------------------------------------------------------------------------
# Константы
# --------------------------------------------------------------------------

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

VENDOR_PAGES = {
    "nvidia": "https://nvidia.com",
    "amd": "https://amd.com",
    "intel": "https://intel.com",
    "realtek": "https://realtek.com",
    "broadcom": "https://broadcom.com",
    "qualcomm": "https://qualcomm.com",
    "other": "",
}

_REALTEK_AUDIO_URL = "https://realtek.com?cate_id=580"
_REALTEK_LAN_URL = "https://realtek.com?cate_id=584"

_NV_API = ("https://geforce.com"
           "services/AjaxDriverService.php")

MODE_UPDATE = "update"        
MODE_CURRENT = "current"      
MODE_MANUAL = "manual"        
MODE_WU = "wu"                
MODE_ERROR = "error"          


class DownloadCanceled(Exception):
    """Пользователь прервал скачивание."""


class NetworkError(Exception):
    """Проблема сети/HTTP при обращении к официальному источнику."""


# --------------------------------------------------------------------------
# Установленные драйверы (WMI)
# --------------------------------------------------------------------------

def get_installed_drivers(progress_cb=None) -> list[dict]:
    """Возвращает установленные драйверы PCI-устройств (Win32_PnPSignedDriver)."""
    # Оптимизировали фильтрацию прямо в PowerShell, чтобы WMI не перегружал буфер памяти
    script = r"""
$OutputEncoding = [Console]::OutputEncoding = [Text.Encoding]::UTF8
$drvs = Get-CimInstance Win32_PnPSignedDriver -ErrorAction SilentlyContinue |
        Where-Object { $_.DeviceID -like 'PCI\VEN_*' -and $_.DriverVersion -ne $null }
foreach ($d in $drvs) {
    [PSCustomObject]@{
        DeviceID = $d.DeviceID
        Name = $d.DeviceName
        Version = $d.DriverVersion
        Date = $d.DriverDate
        Manufacturer = $d.Manufacturer
    } | ConvertTo-Json -Compress
}
"""
    result: list[dict] = []
    code, text = _run_ps(script, timeout=120)
    if code != 0 and not text.strip():
        return result
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        result.append({
            "device_id": obj.get("DeviceID") or "",
            "name": obj.get("Name") or "",
            "version": obj.get("Version") or "",
            "date_yyyymmdd": _cim_date_to_compact(obj.get("Date") or ""),
            "manufacturer": obj.get("Manufacturer") or "",
        })
    result.sort(key=lambda d: d["date_yyyymmdd"], reverse=True)
    if progress_cb:
        progress_cb(f"Установленных записей драйверов: {len(result)}")
    return result


def _cim_date_to_compact(s: str) -> str:
    """'20250617000000.000000-000' → '20250617' (YYYYMMDD)."""
    if not s:
        return ""
    m = re.match(r"^(\d{8})", s)
    return m.group(1) if m else ""


def build_driver_map(installed: list[dict]) -> dict:
    """Карта VEN:DEV → {version, date_yyyymmdd, name}."""
    out: dict[str, dict] = {}
    for d in installed:
        m = re.match(r"PCI\\VEN_([0-9A-Fa-f]{4})&DEV_([0-9A-Fa-f]{4})",
                     d.get("device_id") or "")
        if not m:
            continue
        key = f"{m.group(1).upper()}:{m.group(2).upper()}"
        if key not in out:
            out[key] = d
    return out


# --------------------------------------------------------------------------
# NVIDIA: поиск через официальный API
# --------------------------------------------------------------------------

def _http_get_json(url: str, timeout: int = 30) -> dict:
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://nvidia.com",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", errors="replace")
    except Exception as exc:
        raise NetworkError(f"{type(exc).__name__}: {exc}") from exc
    try:
        return json.loads(raw)
    except Exception as exc:
        raise NetworkError(f"Не удалось разобрать ответ сервера: {raw[:120]}") from exc


def _version_to_number(version: str) -> int:
    """ИСПРАВЛЕНО: Теперь берет последние 5 цифр для точного сопоставления с CIM-драйвером.
    '616.64' -> '61664' -> 61664
    """
    digits = re.sub(r"\D", "", version or "")
    if len(digits) >= 5:
        try:
            return int(digits[-5:])
        except ValueError:
            return 0
    return 0


def _nv_driver_number(cim_version: str) -> int:
    """Из CIM-версии NVIDIA (32.0.15.8266) получает номер драйвера (58266)."""
    digits = re.sub(r"\D", "", cim_version or "")
    if len(digits) >= 5:
        try:
            return int(digits[-5:])
        except ValueError:
            return 0
    return 0


def _parse_release_date(s: str) -> str:
    if not s:
        return ""
    s = s.strip()
    m = re.match(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", s)
    if m:
        return f"{m.group(1)}{int(m.group(2)):02d}{int(m.group(3)):02d}"
    m = re.match(r"^(\d{1,2})[/.](\d{1,2})[/.](\d{4})", s)
    if m:
        return f"{m.group(3)}{int(m.group(1)):02d}{int(m.group(2)):02d}"
    m = re.match(r"^(?:[A-Za-z]{3},?\s+)?([A-Za-z]{3})\s+(\d{1,2}),?\s+(\d{4})", s)
    if m:
        months = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
                  "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
        mon = months.get(m.group(1).lower()[:3])
        if mon:
            return f"{m.group(3)}{mon:02d}{int(m.group(2)):02d}"
    return ""


def nvidia_lookup_driver(progress_cb=None) -> dict:
    """Запрашивает у NVIDIA официальный актуальный драйвер для настольных GeForce."""
    params = {
        "func": "DriverManualLookup",
        "psid": "101",          
        "pfid": "929",          
        "osID": "57",           
        "languageCode": "1033",
        "isWHQL": "1",
        "dch": "1",
        "sort1": "0",
        "numberOfResults": "1",
    }
    url = _NV_API + "?" + urllib.parse.urlencode(params)
    if progress_cb:
        progress_cb("Запрос к официальному API NVIDIA…")
    data = _http_get_json(url)
    ids = data.get("IDS") or []
    if not ids:
        raise NetworkError(f"NVIDIA API не вернул результатов (Total={data.get('Total')}).")
    info = (ids[0].get("downloadInfo") or {})
    dl_url = info.get("DownloadURL") or ""
    version = info.get("Version") or ""
    if not dl_url or not version:
        raise NetworkError("NVIDIA API вернул неполные данные.")
    return {
        "vendor": "nvidia",
        "version": version,
        "release_date": _parse_release_date(info.get("ReleaseDateTime") or ""),
        "url": dl_url,
        "filename": os.path.basename(urllib.parse.urlsplit(dl_url).path),
        "product": info.get("ProductName") or info.get("DeviceToProductFamilyName") or "",
    }


# --------------------------------------------------------------------------
# Построение состояния по каждому компоненту
# --------------------------------------------------------------------------

def _manual_page(component: dict) -> str:
    vendor = component.get("vendor", "")
    kind = component.get("kind", "")
    if vendor == "nvidia":
        return VENDOR_PAGES["nvidia"]
    if vendor == "realtek":
        if kind == "audio":
            return _REALTEK_AUDIO_URL
        if kind == "network":
            return _REALTEK_LAN_URL
        return VENDOR_PAGES["realtek"]
    return VENDOR_PAGES.get(vendor, "")


def _manual_note(component: dict) -> str:
    vendor = component.get("vendor", "")
    kind = component.get("kind", "")
    name = component.get("name", "")
    low = name.lower()
    if vendor == "intel" and kind in ("chipset", "storage"):
        return "Чипсет/контроллеры Intel работают со встроенными драйверами Windows; обновления приходят через Windows Update."
    if vendor == "realtek" and kind == "audio":
        return "Звук Realtek: системный драйвер Windows обычно достаточен; обновления приходят через Windows Update."
    if "nvidia high definition audio" in low or "nvidia virtual audio" in low:
        return "Обновляется вместе с драйвером видеокарты NVIDIA (см. видеокарту)."
    if kind == "usb":
        return "USB-контроллеры используют встроенные драйверы Windows."
    if kind == "wifi":
        return "Обновления Wi-Fi/Bluetooth приходят через Windows Update."
    return "Системные драйверы Windows; обновления приходят через Windows Update."


def build_state(component: dict, installed: dict, candidate: dict | None,
                error: str = "") -> dict:
    """Формирует строку результата для таблицы интерфейса.
    
    Сопоставляет данные аппаратного сканирования с установленными драйверами 
    и доступными обновлениями.
    """
    vendor = component.get("vendor", "")
    kind = component.get("kind", "")
    state = {
        "component": component,
        "vendor": vendor,
        "kind": kind,
        "name": component.get("name", ""),
        "installed_version": (installed.get("version") or "—"),
        "installed_date": (installed.get("date_yyyymmdd") or ""),
        "mode": MODE_ERROR,
        "status_text": "",
        "candidate": None,
        "manual_url": _manual_page(component),
        "manual_note": "",
        "error": error,
    }

    # Видеокарта NVIDIA — единственный компонент с автоматическим обновлением
    is_nv_gpu = vendor == "nvidia" and kind == "gpu"
    if is_nv_gpu:
        if error:
            state["mode"] = MODE_ERROR
            state["status_text"] = f"⚠ Не удалось проверить: {error}"
            state["manual_note"] = "Откройте страницу NVIDIA для ручной проверки."
            return state
        if not candidate:
            state["mode"] = MODE_ERROR
            state["status_text"] = "⚠ Сервис NVIDIA не ответил"
            state["manual_note"] = "Проверьте соединение и попробуйте снова."
            return state
            
        state["candidate"] = candidate
        inst_date = state["installed_date"]
        cand_date = candidate.get("release_date", "")
        
        # 1) Если есть обе даты — сравниваем даты выпуска
        up_to_date = bool(inst_date and cand_date and inst_date >= cand_date)
        if not up_to_date:
            # 2) Числовое сравнение версий по последним 5 цифрам (исправлено)
            inst_num = _nv_driver_number(state["installed_version"])
            cand_num = _version_to_number(candidate.get("version", ""))
            if inst_num and cand_num:
                up_to_date = inst_num >= cand_num
                
        if up_to_date:
            state["mode"] = MODE_CURRENT
            state["status_text"] = "✅ Актуальная версия"
            state["manual_note"] = f"Установлен драйвер {state['installed_version']} — новее официального релиза нет."
        else:
            state["mode"] = MODE_UPDATE
            state["status_text"] = "🔁 Доступно обновление"
            state["manual_note"] = f"Официальный драйвер NVIDIA {candidate['version']} можно скачать и установить автоматически."
        return state

    # NVIDIA HD Audio — часть пакета видеодрайвера (обновляется вместе с GPU)
    low_name = (component.get("name") or "").lower()
    if vendor == "nvidia" and ("high definition audio" in low_name or "virtual audio" in low_name):
        state["mode"] = MODE_MANUAL
        state["status_text"] = "🖥 См. видеокарту"
        state["manual_note"] = "Обновляется автоматически вместе с драйвером видеокарты NVIDIA (отметьте видеокарту выше)."
        return state

    # Остальные компоненты обновляются штатно через Windows Update
    state["mode"] = MODE_WU
    if vendor == "other":
        state["status_text"] = "ℹ Системный (Windows Update)"
    else:
        state["status_text"] = "🔁 Через Windows Update"
    state["manual_note"] = _manual_note(component)
    state["manual_note"] += "\nОбновление выполняется штатным Центром обновления Windows — без регистрации и сторонних программ."
    return state

def scan_driver_status(progress_cb=None, cancel=None) -> list[dict]:
    """Возвращает список состояний компонентов с найденными обновлениями."""
    def cb(text):
        if progress_cb:
            progress_cb(text)

    cb("Шаг 1/3 — определение компонентов (видео, звук, сеть, чипсет)…")
    components = hardware_scan.scan_hardware(progress_cb=None, cancel=cancel)
    if cancel is not None and cancel.is_set():
        return []
        
    cb(f"Найдено компонентов: {len(components)}. Определяю установленные драйверы…")
    installed = get_installed_drivers(progress_cb=cb)
    if cancel is not None and cancel.is_set():
        return []
    drv_map = build_driver_map(installed)

    states: list[dict] = []
    for i, comp in enumerate(components):
        if cancel is not None and cancel.is_set():
            return []
        key = f"{comp.get('ven', '')}:{comp.get('dev', '')}"
        inst = drv_map.get(key, {})
        candidate = None
        error = ""
        
        if comp.get("vendor") == "nvidia" and comp.get("kind") == "gpu":
            try:
                candidate = nvidia_lookup_driver(progress_cb=cb)
            except Exception as exc:
                error = str(exc)
                
        states.append(build_state(comp, inst, candidate, error=error))
        cb(f"Обработано компонентов: {i + 1}/{len(components)}")
        
    return states


# --------------------------------------------------------------------------
# Скачивание
# --------------------------------------------------------------------------

def download_file(url: str, dest: str, progress_cb=None, cancel=None,
                  chunk: int = 256 * 1024, retries: int = 3) -> int:
    """Скачивает файл по URL с прогрессом, отменой и автоповторами."""
    existing = 0
    tmp = dest + ".part"
    if os.path.exists(tmp):
        try:
            existing = os.path.getsize(tmp)
        except OSError:
            existing = 0
            
    attempt = 0
    last_error: Exception | None = None
    while True:
        if cancel is not None and cancel.is_set():
            raise DownloadCanceled()
        attempt += 1
        try:
            done = _download_once(url, tmp, existing, progress_cb, cancel, chunk)
            os.replace(tmp, dest)
            return done
        except DownloadCanceled:
            raise
        except NetworkError as exc:
            last_error = exc
            if cancel is not None and cancel.is_set():
                raise DownloadCanceled()
            if attempt > retries:
                raise NetworkError(f"Скачивание прервано после {retries} попыток: {exc}")
            try:
                existing = os.path.getsize(tmp) if os.path.exists(tmp) else 0
            except OSError:
                existing = 0
            if progress_cb:
                progress_cb(existing, 0, 0.0)
            time.sleep(1.5 * attempt)
            
    assert last_error is not None
    raise last_error


def _download_once(url: str, tmp: str, offset: int, progress_cb, cancel,
                   chunk: int) -> int:
    """Одна низкоуровневая сессия скачивания с поддержкой HTTP Range."""
    headers = {
        "User-Agent": UA,
        "Accept": "*/*",
        "Referer": "https://nvidia.com",
    }
    if offset:
        headers["Range"] = f"bytes={offset}-"
        
    req = urllib.request.Request(url, headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=60)
    except Exception as exc:
        raise NetworkError(f"{type(exc).__name__}: {exc}") from exc

    total = 0
    status = getattr(resp, "status", 200)
    try:
        cl = int(resp.headers.get("Content-Length") or 0)
    except (ValueError, TypeError):
        cl = 0

    if status == 206:
        total = offset + cl
    else:
        offset = 0
        total = cl

    done = offset
    last_t = time.time()
    last_b = float(done)
    speed = 0.0
    
    try:
        mode = "wb" if offset == 0 else "ab"
        with open(tmp, mode) as f:
            while True:
                if cancel is not None and cancel.is_set():
                    raise DownloadCanceled()
                block = resp.read(chunk)
                if not block:
                    break
                f.write(block)
                done += len(block)
                now = time.time()
                if now - last_t >= 0.4:
                    speed = (done - last_b) / (now - last_t)
                    last_t = now
                    last_b = float(done)
                if progress_cb:
                    progress_cb(done, total, speed)
    except DownloadCanceled:
        raise
    except Exception as exc:
        raise NetworkError(f"Соединение оборвано: {exc}") from exc
    finally:
        try:
            resp.close()
        except Exception:
            pass

    if total and done < total:
        raise NetworkError(f"Соединение оборвано до завершения ({done} из {total} байт).")
    return done


def default_download_dir() -> str:
    base = os.path.join(os.environ.get("TEMP", tempfile.gettempdir()),
                        "DiskCleaner", "Drivers")
    os.makedirs(base, exist_ok=True)
    return base


# --------------------------------------------------------------------------
# Установка (тихая, через UAC)
# --------------------------------------------------------------------------

def run_installer(exe_path: str, args: list[str] | None = None) -> tuple[bool, str]:
    """Запускает установщик от имени администратора (запрос UAC) и ждёт завершения."""
    args = list(args) if args else ["-s", "-noreboot"]
    if not os.path.isfile(exe_path):
        return False, f"Файл установщика не найден: {exe_path}"

    # ИСПРАВЛЕНО: Восстановлены корректные служебные поля си-структур _pack_ и _fields_
    class _SHELLEXECUTEINFOW(ctypes.Structure):
        _pack_ = 1
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("fMask", ctypes.c_ulong),
            ("hwnd", wintypes.HWND),
            ("lpVerb", wintypes.LPCWSTR),
            ("lpFile", wintypes.LPCWSTR),
            ("lpParameters", wintypes.LPCWSTR),
            ("lpDirectory", wintypes.LPCWSTR),
            ("nShow", ctypes.c_int),
            ("hInstApp", wintypes.HINSTANCE),
            ("lpIDList", ctypes.c_void_p),
            ("lpClass", wintypes.LPCWSTR),
            ("hkeyClass", ctypes.c_void_p),
            ("dwHotKey", wintypes.DWORD),
            ("hIcon", ctypes.c_void_p),
            ("hProcess", wintypes.HANDLE),
        ]

    SEE_MASK_NOCLOSEPROCESS = 0x00000040
    SEE_MASK_NOASYNC = 0x00000100
    SW_HIDE = 0
    ERROR_CANCELLED = 1223
    
    params = " ".join(_quote_arg(a) for a in args)
    sei = _SHELLEXECUTEINFOW()
    sei.cbSize = ctypes.sizeof(_SHELLEXECUTEINFOW)
    sei.fMask = SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NOASYNC
    sei.hwnd = None
    sei.lpFile = exe_path
    sei.lpParameters = params
    sei.lpDirectory = os.path.dirname(exe_path) or None
    sei.nShow = SW_HIDE
    if not _is_admin():
        sei.lpVerb = "runas"
        
    ok = ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(sei))
    if not ok:
        err = int(ctypes.windll.kernel32.GetLastError() or 0)
        if err == ERROR_CANCELLED:
            return False, "Установка отменена (не подтверждён запрос UAC)."
        return False, f"Не удалось запустить установщик (код ошибки {err})."
        
    h_proc = sei.hProcess
    if not h_proc:
        return False, "Не удалось получить процесс установщика."
        
    try:
        ctypes.windll.kernel32.WaitForSingleObject(h_proc, 0xFFFFFFFF)
        exit_code = wintypes.DWORD(0)
        ctypes.windll.kernel32.GetExitCodeProcess(h_proc, ctypes.byref(exit_code))
        ec = int(exit_code.value)
    finally:
        ctypes.windll.kernel32.CloseHandle(h_proc)
        
    if ec == 0:
        return True, "Установка завершена успешно."
    return False, f"Установщик завершился с кодом {ec}."


def _quote_arg(arg: str) -> str:
    arg = str(arg)
    if '"' in arg:
        arg = arg.replace('"', '\\"')
    return f'"{arg}"'


def _is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


# ИСПРАВЛЕНО: Функция корректно завершена и возвращает булево значение успеха
def open_manual_page(url: str) -> bool:
    """Открывает официальную страницу в браузере."""
    import webbrowser
    if not url:
        return False
    try:
        webbrowser.open(url)
        return True
    except Exception:
        return False
