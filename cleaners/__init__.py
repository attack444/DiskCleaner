"""Пакет логики очистки диска."""

from . import (
    registry_cleaner,
    report,
    safety,
    smart_rules,
    startup,  # новый модуль
    uninstaller,
    utils,
)
from .disk_scan import (
    BigFile,
    EmptyFolder,
    analyze_extension_types,
    find_big_files,
    find_empty_folders,
    unified_scan,
)
from .duplicate_finder import DuplicateFinder, DuplicateGroup
from .full_check import run_full_check
from .system_cleaner import JunkCategory, SystemCleaner

__all__ = [
    "utils",
    "safety",
    "report",
    "smart_rules",
    "registry_cleaner",
    "uninstaller",
    "JunkCategory",
    "SystemCleaner",
    "DuplicateFinder",
    "DuplicateGroup",
    "BigFile",
    "EmptyFolder",
    "find_big_files",
    "find_empty_folders",
    "analyze_extension_types",
    "unified_scan",
    "run_full_check",
    "startup",
]
