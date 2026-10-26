r"""Приложение обработки съёмки капель: просмотр кадров записей и заполнение таблицы записей.

Запуск:  .venv\Scripts\python.exe app\run.py     (обычный Python: py app\run.py)
Эксперимент — из experiments/current.py (в окне можно выбрать другой).

Окно — app/window.py, вкладка «Записи» — app/records_page.py, кадр и его расчёт — app/frame_view.py.
Кадры считаются в фоновых процессах; каждый процесс заново запускает этот файл, поэтому здесь на уровне
модуля — только самое необходимое, а окно создаётся в main().
"""

import os
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # не создавать папки __pycache__
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"  # и в фоновых процессах расчёта кадров
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # чтобы находились папки lib, app и experiments


def main() -> int:
    """Открыть окно; возвращает код выхода."""
    import logging

    from PySide6.QtWidgets import QApplication

    from app.window import MainWindow

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    application = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return application.exec()


if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()  # для будущего exe
    sys.exit(main())
