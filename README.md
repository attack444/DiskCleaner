# 🧹 DiskCleaner & Driver Updater

[English](#english) | [Русский](#русский)

---

## Русский

**DiskCleaner** — это мощная, легковесная и абсолютно бесплатная утилита с открытым исходным кодом для комплексной очистки операционной системы Windows и безопасного обновления драйверов. 

Программа написана на чистом Python с использованием стандартных графических библиотек (`Tkinter`) и низкоуровневых интерфейсов Win32 API (`ctypes`), что гарантирует молниеносную скорость работы и отсутствие тяжелых зависимостей.

### ✨ Ключевые возможности

*   **⚡ Полная проверка:** Анализ всей системы в один клик (мусор, дубликаты, большие файлы, пустые папки).
*   **🧹 Очистка системы:** Безопасное удаление кэша браузеров (Chrome, Edge, Firefox), системных дампов памяти (BSOD), логов и временных каталогов `%TEMP%`.
*   **🔁 Поиск дубликатов:** Безопасный двухэтапный алгоритм поиска копий по содержимому (MD5) с побайтовой верификацией совпадений перед удалением.
*   **🗜 Большие файлы:** Мгновенный вывод топа самых «тяжелых» файлов в выбранной директории с указанием даты изменения.
*   **📂 Пустые папки:** Поиск и удаление пустых разветвленных каталогов.
*   **🔄 Автообновление драйверов:** Полная тихая автоматизация для видеокарт NVIDIA (скачивание с официального сервера и установка) и безопасная маршрутизация остальных компонентов через штатный Windows Update.

### 🛠 Архитектурные особенности (Для разработчиков)

*   **Асинхронность:** Все тяжелые дисковые и сетевые операции вынесены в фоновые потоки (`threading.Thread`), графический интерфейс Tkinter никогда не «фризится» и мгновенно реагирует на кнопку «Отмена».
*   **Win32 API & ctypes:** Перенос файлов в Корзину и её очистка реализованы через оригинальные макросы Windows (`SHFileOperationW`, `SHQueryRecycleBinW`).
*   **Высокая производительность:** Сканирование файловой таблицы оптимизировано через итеративный `os.scandir` вместо тяжелого `os.walk`, что снижает нагрузку на диск в 3-4 раза.

### 🚀 Запуск из исходников

Проект использует современный менеджер пакетов `uv`. Для запуска выполните:

```bash
# Клонировать репозиторий
git clone https://github.com
cd DiskCleaner

# Запуск приложения
uv run main.py
```

### 📦 Компиляция в EXE

Для сборки независимого исполняемого файла без установки Python в систему:
```bash
uvx pyinstaller DiskCleaner.spec
```

---

## English

**DiskCleaner** is a powerful, lightweight, and completely free open-source utility designed for comprehensive Windows system optimization, junk cleanup, and safe driver updates. 

Built using pure Python with native `Tkinter` for the GUI and low-level Win32 API interfaces (`ctypes`), it guarantees blazing-fast execution and a zero-dependency footprint.

### ✨ Key Features

*   **⚡ Full Check:** One-click global analysis of system junk, duplicate files, large objects, and empty directories.
*   **🧹 System Cleanup:** Safe removal of browser caches (Chrome, Edge, Firefox), crash dumps, log files, and temporary `%TEMP%` directories.
*   **🔁 Duplicate Finder:** Secure two-stage hash-matching (MD5) algorithm with partial pre-filtering and full byte-verification to prevent data loss.
*   **🗜 Large Files:** Instant breakdown of the heaviest files in a selected directory, including file modification metadata.
*   **📂 Empty Folders:** Scan and recursive deletion of hollow directory trees.
*   **🔄 Driver Automation:** Full silent update automation for NVIDIA GPUs (direct downloads from official servers) and safe native integration with Windows Update for other hardware components.

### 🚀 Installation & Running

The project is managed via the modern `uv` package manager. To run from source:

```bash
git clone https://github.com
cd DiskCleaner
uv run main.py
```

### 📦 Building Standalone EXE

To compile the application into a standalone executable file:
```bash
uvx pyinstaller DiskCleaner.spec
```

### 📄 License
This project is licensed under the **MIT License** — feel free to use, modify, and distribute.
