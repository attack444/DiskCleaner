#!/usr/bin/env python3
"""Точка входа приложения «Очиститель и анализатор диска»."""
from __future__ import annotations

import os
import sys

# Добавляем корень проекта в путь, чтобы работали импорты cleaners и ui.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ui.app import run  # noqa: E402


if __name__ == "__main__":
    run()
