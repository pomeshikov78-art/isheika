"""Запуск «Ищейки» одним файлом — точка входа для сборки под Windows.

Собранный exe не требует установленного Python: внутри и интерпретатор,
и зависимости. Задачи лаунчера:
  * при первом запуске создать рядом файл настроек из образца;
  * поднять сервер и открыть браузер;
  * не закрывать окно при ошибке, иначе пользователь не увидит причину.
"""
from __future__ import annotations

import os
import shutil
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

APP_NAME = "Ищейка"
DEFAULT_PORT = 8123


def use_utf8_console() -> None:
    """Переводит консоль Windows в UTF-8.

    По умолчанию она работает в cp1252 или cp866, и первая же русская буква
    в выводе роняет программу с UnicodeEncodeError - собранный exe закрывался,
    не успев ничего показать.
    """
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception:
            pass

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


use_utf8_console()


def base_dir() -> Path:
    """Каталог рядом с exe (или с исходником при обычном запуске).

    Настройки, база и отчёты должны лежать рядом с программой, а не внутри
    временной распаковки PyInstaller: иначе они исчезнут при следующем запуске.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


def bundled_dir() -> Path:
    """Каталог с упакованными ресурсами (шаблоны, веб-интерфейс)."""
    return Path(getattr(sys, "_MEIPASS", base_dir()))


def ensure_env(root: Path) -> bool:
    """Создаёт .env из образца при первом запуске. True - если создан сейчас."""
    env_file = root / ".env"
    if env_file.exists():
        return False

    sample = bundled_dir() / ".env.example"
    if sample.exists():
        shutil.copyfile(sample, env_file)
    else:
        env_file.write_text("LLM_PROVIDER=mock\n", encoding="utf-8")
    return True


def free_port(preferred: int) -> int:
    """Занятый порт - обычная ситуация: берём следующий свободный."""
    for candidate in range(preferred, preferred + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            if probe.connect_ex(("127.0.0.1", candidate)) != 0:
                return candidate
    return preferred


def open_browser_later(url: str, delay: float = 3.0) -> None:
    def opener() -> None:
        time.sleep(delay)
        webbrowser.open(url)

    threading.Thread(target=opener, daemon=True).start()


def banner(root: Path, url: str, first_run: bool) -> None:
    line = "=" * 66
    print(line)
    print(f"  {APP_NAME} — мультиагентная система анализа и прогнозирования")
    print(line)
    print(f"  Интерфейс:   {url}")
    print(f"  Документация API: {url}/docs")
    print(f"  Рабочий каталог:  {root}")
    print(line)

    if first_run:
        print("  ПЕРВЫЙ ЗАПУСК. Рядом с программой создан файл настроек .env")
        print()
        print("  Сейчас система работает на заглушке: отчёты будут")
        print("  демонстрационными. Чтобы включить реальный анализ, впишите")
        print("  в .env бесплатный ключ и перезапустите программу:")
        print()
        print("      LLM_PROVIDER=groq")
        print("      GROQ_API_KEY=gsk_...        (console.groq.com)")
        print()
        print("  Поисковые движки ключей не требуют и работают сразу.")
        print(line)

    print("  Закрыть программу: Ctrl+C или закрыть это окно")
    print(line, flush=True)


def main() -> int:
    root = base_dir()
    os.chdir(root)

    first_run = ensure_env(root)
    # Пути настроек и хранилища задаются до импорта приложения:
    # конфигурация читается на импорте модуля
    os.environ.setdefault("SLEUTH_HOME", str(root))

    port = free_port(int(os.environ.get("SLEUTH_PORT", DEFAULT_PORT)))
    url = f"http://127.0.0.1:{port}"

    banner(root, url, first_run)
    open_browser_later(url)

    try:
        import uvicorn

        from app.main import app

        uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    except KeyboardInterrupt:
        print("\nОстановлено пользователем.")
        return 0
    except Exception as error:  # окно не должно закрыться молча
        print("\n" + "!" * 66)
        print(f"  Не удалось запустить {APP_NAME}: {type(error).__name__}: {error}")
        print("!" * 66)
        import traceback

        traceback.print_exc()
        input("\nНажмите Enter, чтобы закрыть окно...")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
