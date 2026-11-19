"""Создание нового эксперимента без окон: папка в experiments/, settings.py и thresholds.py из образца,
таблица записей xlsx.

settings.py копируется из выбранного эксперимента-образца; заменяются только пути (IMAGES_DIR,
TABLE_FILE, OUTPUT_DIR), CONDITIONS и RECORDS очищаются, MATLAB_RESULTS_DIR закомментируется —
чужие результаты MATLAB новому эксперименту не нужны. thresholds.py копируется побайтно.

Таблица создаётся по тем же правилам, что читает приложение (lib/common/table_edit.py): первый лист,
строка 1 — заголовки, строка 2 — описания, данные с 3-й строки. Колонки — стандартные (TABLE_COLUMNS)
и те, что в settings.py заданы именами колонок таблицы (FPS = "fps" и т. п., record_setting). Строки —
подпапки папки кадров, в которых есть картинки: rec_name и images_path_side — имя подпапки.
"""

import keyword
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from openpyxl import Workbook

from .config_edit import SettingsFile, parse_settings, value_text
from .files import IMAGE_FORMATS
from .records import project_path

# Стандартные колонки новой таблицы: заголовок и описание (2-я строка xlsx).
TABLE_COLUMNS = [
    ("cond_num", "номер условий (для выбора записей CONDITIONS в settings.py)"),
    ("rec_name", "имя записи"),
    ("images_path_side", "путь к кадрам относительно IMAGES_DIR; если не оканчивается именем записи, оно добавляется"),
    ("start_frame", "первый кадр диапазона (номер файла, с 1)"),
    ("fin_frame", "последний кадр диапазона (номер файла)"),
    ("rim", "порог площади рима и крупных брызг, px²"),
    ("c_x", "начало координат st4 по x, px"),
    ("c_y", "начало координат st4 по y, px"),
]

# Настройки settings.py, значение которых может быть именем колонки таблицы (record_setting).
COLUMN_SETTINGS = ("FPS", "SCALE_MM_PER_PX", "SURFACE_ROW", "BACKGROUND_FRAME")

# Описания колонок, которые задаются настройкой-колонкой.
COLUMN_SETTING_NOTES = {
    "FPS": "кадров в секунду записи (settings.py: FPS — имя этой колонки)",
    "SCALE_MM_PER_PX": "масштаб записи, мм на пиксель (settings.py: SCALE_MM_PER_PX — имя этой колонки)",
    "SURFACE_ROW": "строка кадра (с 1), начиная с которой вниз — вода (settings.py: SURFACE_ROW — имя этой колонки)",
    "BACKGROUND_FRAME": "номер файла фона записи, с 1 (settings.py: BACKGROUND_FRAME — имя этой колонки)",
}

MATLAB_SETTING = re.compile(r"^(\s*)MATLAB_RESULTS_DIR(\s*=)", re.M)  # строка MATLAB_RESULTS_DIR = ...


def check_name(name: str) -> str:
    """Что не так с именем эксперимента ('' — годится): пусто, не идентификатор Python, начинается
    с «_», занято файлом experiments/current.py."""
    if not name:
        return "Введите имя эксперимента"
    if name.startswith("_"):
        return "Имя эксперимента не должно начинаться с «_»"
    if not name.isidentifier() or keyword.iskeyword(name):
        return "Имя эксперимента: буквы, цифры и «_», без пробелов; не начинать с цифры и не слово Python"
    if name == "current":
        return "Имя «current» занято файлом experiments/current.py"
    return ""


def table_columns(settings_text: str) -> list[tuple[str, str]]:
    """Колонки новой таблицы: стандартные (TABLE_COLUMNS) и заданные именами колонок в settings_text
    (FPS = "fps", SURFACE_ROW = "surface_row" и т. п.); у каждой — заголовок и описание."""
    columns = list(TABLE_COLUMNS)
    headers = {header for header, _ in columns}
    for setting in parse_settings(settings_text):
        if setting.name in COLUMN_SETTINGS and isinstance(setting.value, str):
            header = setting.value.strip()
            if header and header not in headers:
                columns.append((header, COLUMN_SETTING_NOTES.get(setting.name, f"значение {setting.name} для записи")))
                headers.add(header)
    return columns


def _rows_by_subfolders(images_dir: Path) -> list[dict[str, str]]:
    """Строки новой таблицы: подпапки папки кадров, в которых есть картинки (по алфавиту);
    rec_name и images_path_side — имя подпапки."""
    rows = []
    for folder in sorted(Path(images_dir).iterdir(), key=lambda path: path.name.lower()):
        if not folder.is_dir() or folder.name.startswith((".", "_")):
            continue
        if any(p.is_file() and p.suffix.lower() in IMAGE_FORMATS for p in folder.iterdir()):
            rows.append({"rec_name": folder.name, "images_path_side": folder.name})
    return rows


def create_table(path: Path, columns: list[tuple[str, str]], rows: list[dict[str, Any]] | None = None) -> None:
    """Создать таблицу записей xlsx: строка 1 — заголовки, строка 2 — описания, дальше строки
    (словари заголовок → значение); папки пути создаются. Существующий файл не проверяется и не
    перезаписывается — вызывающий сам решает, создавать ли."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    sheet = workbook.active
    sheet.append([header for header, _ in columns])
    sheet.append([description for _, description in columns])
    for row in rows or ():
        sheet.append([row.get(header) for header, _ in columns])
    workbook.save(path)


def create_experiment(experiments_dir: Path, name: str, images_dir: str | Path, table_file: str | Path,
                      output_dir: str | Path, source: str) -> Path:
    """Создать эксперимент name в папке experiments_dir из образца source.

    images_dir — папка кадров (IMAGES_DIR), table_file — таблица записей (если файла нет, она будет
    создана: колонки с описаниями, строки — подпапки images_dir с картинками), output_dir — папка
    результатов. Пути — полные или от папки проекта. Возвращает папку нового эксперимента.
    """
    experiments_dir = Path(experiments_dir)
    error = check_name(name)
    if error:
        raise ValueError(error)
    folder = experiments_dir / name
    if folder.exists():
        raise FileExistsError(f"Папка {folder} уже есть — выберите другое имя")
    source_dir = experiments_dir / source
    for part in ("settings.py", "thresholds.py"):
        if not (source_dir / part).is_file():
            raise FileNotFoundError(f"Нет эксперимента-образца «{source}»: нет файла {source_dir / part}")
    images = project_path(images_dir)
    if not images.is_dir():
        raise FileNotFoundError(f"Нет папки с кадрами: {images}")
    table = project_path(table_file)
    if table.suffix.lower() != ".xlsx":
        raise ValueError(f"Таблица записей должна быть файлом .xlsx: {table}")
    output = project_path(output_dir)

    source_text = (source_dir / "settings.py").read_bytes().decode("utf-8")
    folder.mkdir(parents=True)
    shutil.copyfile(source_dir / "thresholds.py", folder / "thresholds.py")
    header = (f"# Эксперимент «{name}» — создан {datetime.now():%Y-%m-%d}; настройки и пороги "
              f"скопированы из «{source}».\n")
    settings_path = folder / "settings.py"
    settings_path.write_bytes((header + MATLAB_SETTING.sub(r"# \g<0>", source_text)).encode("utf-8"))
    settings = SettingsFile(settings_path)
    for key, value in (("IMAGES_DIR", str(images)), ("TABLE_FILE", str(table)), ("OUTPUT_DIR", str(output))):
        if key not in settings.settings:
            raise ValueError(f"В settings.py образца «{source}» нет {key}")
        settings.set_text(key, value_text(value))
    for key in ("CONDITIONS", "RECORDS"):
        if key in settings.settings:
            settings.set_text(key, "[]")
    settings.save()

    if not table.exists():
        create_table(table, table_columns(source_text), _rows_by_subfolders(images))
    output.mkdir(parents=True, exist_ok=True)
    return folder
