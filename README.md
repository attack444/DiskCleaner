# 🧹 DiskCleaner

**Мощная и безопасная очистка диска Windows** — находит мусор, дубликаты,
крупные файлы и пустые папки, показывает карту занятого места, чистит реестр
и управляет автозагрузкой. Всё — локально, без облака, без регистрации.

![version](https://img.shields.io/badge/version-3.0.0-blue)
![platform](https://img.shields.io/badge/platform-Windows%2010%2F11-informational)
![python](https://img.shields.io/badge/python-3.11%2B-blue)
![license](https://img.shields.io/badge/license-MIT-green)
![downloads](https://img.shields.io/github/downloads/attack444/DiskCleaner/total)
![release](https://img.shields.io/github/v/release/attack444/DiskCleaner)
![issues](https://img.shields.io/github/issues/attack444/DiskCleaner)

---

## ✨ Возможности

### 📊 Анализ и очистка диска
- **Умный поиск мусора** — кэш браузеров, шейдеры GPU, дампы сбоев, обновления Windows.
- **Интеллектуальная защита** — игровые ассеты (Steam/Epic/GOG), системные файлы,
  среды разработки (Git, Python, node_modules) не попадают в список удаления.
- **Три профиля сканирования** — Быстро / Обычно / Глубоко.
- **Предпросмотр перед удалением** и **Пробный прогон** (dry-run).
- **Белый список** — добавьте путь один раз, больше его никто не тронет.
- **Журнал удалений** — что удалено, когда, размер — всё в `%TEMP%\DiskCleaner`.
- Всё, что можно, уходит **в Корзину Windows** — восстанавливается штатно.

### 💾 Анализатор места
- **Treemap-визуализация** занятого места.
- Быстрый спуск по дереву папок двойным кликом.

### ⚡ Автозагрузка
- Реестр (HKCU/HKLM, x86/x64) + папки Startup.
- Отключение безопасное и обратимое.

### 🗄 Чистка реестра
- MuiCache и App Paths — устаревшие ссылки на удалённые программы.

### 📦 Деинсталлятор
- Полный список установленных программ из реестра.
- Бэкап ветки реестра перед удалением.
- Поиск и удаление «хвостов» после штатного деинсталлятора.

### 📄 Отчёты
- Экспорт в HTML (открывается в браузере) и CSV (Excel).

---

## 🚀 Установка

### Готовый exe
Скачай `DiskCleaner.exe` из [Releases](https://github.com/attack444/DiskCleaner/releases).

Требования:
- Windows 10 / 11 (x64)
- .NET не требуется
- Права администратора — **опционально** (нужны для очистки `C:\Windows\Temp`)

### Из исходников
```powershell
git clone https://github.com/USERNAME/DiskCleaner.git
cd DiskCleaner

# С uv (рекомендуется)
uv sync
uv run python main.py

# Или чистым pip
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py