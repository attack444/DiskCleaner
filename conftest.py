"""Общие фикстуры и настройка путей для тестов.

Добавляет корень проекта в sys.path, чтобы `import cleaners` работал
и из корня, и из CI, где pytest запускается из другого cwd.
"""

from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
