"""Определение ключевых компонентов ПК (GPU, звук, сеть, Wi-Fi, чипсет и т.д.)"""
from __future__ import annotations

import re
import json
import os

# Предполагаем, что _run_ps импортируется так же.
# Важно: убедитесь, что _run_ps внутри использует subprocess.communicate() или потоковое чтение во избежание дедлоков!
from .drivers import _decode, _run_ps

# (ключ, русское имя, фильтр по классу PnP)
_COMPONENT_FILTERS = [
    ("gpu", "Видеокарта", ["Display"]),
    ("audio", "Звук", ["Media", "MEDIA"]),
    ("network", "Сеть (Ethernet)", ["Net", "NET"]),
    ("wifi", "Wi-Fi / Bluetooth", ["NET", "Bluetooth"]),
    ("chipset", "Чипсет / системная логика", ["System", "HDC"]),
    ("storage", "Накопители / NVMe", ["SCSIAdapter", "HDC", "DiskDrive"]),
    ("usb", "USB-контроллеры", ["USB"]),
]

def _detect_vendor(instance_id: str, name: str) -> str:
    """Возвращает ключ вендора: nvidia | amd | intel | realtek | qualcomm | other."""
    hay = f"{instance_id} {name}".lower()
    if "ven_10de" in hay or "nvidia" in hay:
        return "nvidia"
    if any(x in hay for x in ["ven_1002", "ven_1022", "amd", "radeon"]):
        return "amd"
    if any(x in hay for x in ["ven_8086", "ven_8087", "intel"]):
        return "intel"
    if "ven_10ec" in hay or "realtek" in hay:
        return "realtek"
    if "ven_14e4" in hay or "broadcom" in hay:
        return "broadcom"
    if "qualcomm" in hay or "ven_17cb" in hay:
        return "qualcomm"
    return "other"


def _parse_ids(instance_id: str) -> dict:
    """Извлекает VEN_XXXX, DEV_XXXX, SUBSYS и REV из Instance ID."""
    ven = re.search(r"VEN_([0-9A-Fa-f]{4})", instance_id)
    dev = re.search(r"DEV_([0-9A-Fa-f]{4})", instance_id)
    sub = re.search(r"SUBSYS_([0-9A-Fa-f]{8})", instance_id)
    rev = re.search(r"REV_([0-9A-Fa-f]{2,4})", instance_id) # Добавили ревизию чипа
    
    return {
        "ven": ven.group(1).upper() if ven else "",
        "dev": dev.group(1).upper() if dev else "",
        "subsys": sub.group(1).upper() if sub else "",
        "rev": rev.group(1).upper() if rev else "",
    }


def scan_hardware(progress_cb=None, cancel=None) -> list[dict]:
    """Возвращает список значимых компонентов."""
    devices = _get_pnp_devices_raw(progress_cb=progress_cb, cancel=cancel)
    if cancel is not None and cancel.is_set():
        return []

    result: list[dict] = []
    seen: set[str] = set()

    for d in devices:
        if cancel is not None and cancel.is_set():
            return []
            
        cls = (d.get("class") or "").lower()
        status = (d.get("status") or "").lower()
        
        if not d.get("instance_id"):
            continue
            
        kind = _component_kind(cls, d.get("instance_id") or "", d.get("name") or "")
        if not kind:
            continue

        ids = _parse_ids(d.get("instance_id") or "")
        if not ids["ven"] and not ids["dev"]:
            continue
            
        # Улучшенный уникальный ключ: учитывает ревизию, если нет subsys
        uniq = f"{kind}:{ids['ven']}:{ids['dev']}:{ids['subsys']}:{ids['rev']}"
        if uniq in seen:
            continue
        seen.add(uniq)

        vendor = _detect_vendor(d.get("instance_id") or "", d.get("name") or "")
        name = d.get("name") or "(Неизвестное устройство)"
        
        result.append({
            "kind": kind,
            "name": name,
            "vendor": vendor,
            "instance_id": d.get("instance_id"),
            "ven": ids["ven"],
            "dev": ids["dev"],
            "subsys": ids["subsys"],
            "rev": ids["rev"],
            "class": d.get("class"),
            "status": status,
            "problem": d.get("problem") or "",
        })

    order = [c[0] for c in _COMPONENT_FILTERS]
    result.sort(key=lambda r: order.index(r["kind"]) if r["kind"] in order else 99)
    return result


def _component_kind(pnp_class: str, instance_id: str, name: str) -> str:
    """Определяет категорию по классу и имени."""
    hay = f"{instance_id} {name}".lower()
    
    if pnp_class == "display":
        return "gpu"
    if pnp_class == "media":
        if any(x in hay for x in ["bluetooth", "wireless"]) and "audio" not in hay:
            return "wifi"
        return "audio"
    if pnp_class == "net":
        if any(x in hay for x in ["wireless", "wi-fi", "wlan", "802.11", "bluetooth"]):
            return "wifi"
        return "network"
    if pnp_class == "bluetooth":
        return "wifi"
    if pnp_class in ("system", "hdc"):
        if any(x in hay for x in ["smbus", "lpc controller", "chipset"]):
            return "chipset"
        if any(x in hay for x in ["sata", "ahci", "nvme", "raid", "storage controller"]):
            return "storage"
        return ""
    if pnp_class in ("scsiadapter", "diskdrive"):
        if "nvme" in hay or "ssd" in hay:
            return "storage"
        return ""
    if pnp_class == "usb":
        return "usb"
    return ""


def _get_pnp_devices_raw(progress_cb=None, cancel=None) -> list[dict]:
    """Получает устройства с полей через PowerShell."""
    # Убрали -PresentOnly, чтобы видеть "проблемные" и неинициализированные устройства
    script = r"""
$OutputEncoding = [Console]::OutputEncoding = [Text.Encoding]::UTF8
$devs = Get-PnpDevice -ErrorAction SilentlyContinue
foreach ($d in $devs) {
    $friendly = $null
    if ($d.FriendlyName) { $friendly = $d.FriendlyName }
    elif ($d.Name) { $friendly = $d.Name }
    if (-not $friendly) { $friendly = $d.InstanceId }
    [PSCustomObject]@{
        Id = $d.InstanceId
        Name = $friendly
        Class = $d.Class
        Status = $d.Status
        Problem = $d.Problem
    } | ConvertTo-Json -Compress
}
"""
    devices: list[dict] = []
    code, text = _run_ps(script, timeout=90)
    if code != 0 and not text.strip():
        return devices
        
    lines = text.splitlines()
    for line in lines:
        # Проверка отмены операции пользователем на каждом шаге парсинга строк
        if cancel is not None and cancel.is_set():
            return []
            
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
            devices.append({
                "instance_id": obj.get("Id") or "",
                "name": obj.get("Name") or "",
                "class": obj.get("Class") or "",
                "status": obj.get("Status") or "",
                "problem": obj.get("Problem") or "",
            })
        except Exception:
            continue
            
        if progress_cb and len(devices) % 20 == 0:
            progress_cb(len(devices), f"Загружено устройств: {len(devices)}")
            
    return devices
