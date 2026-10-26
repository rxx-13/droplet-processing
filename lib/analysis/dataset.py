"""Обработка результатов: данные из итоговых файлов st4 многих экспериментов — одна таблица для распределений.

Источник (Source) — результаты одного эксперимента: таблица записей TABLE_FILE и папка результатов —
OUTPUT_DIR (результаты Python) или, если попросить (matlab=True), MATLAB_RESULTS_DIR (результаты MATLAB, тот же
формат; приложение их пока не показывает). Эксперименты находятся сами: папки experiments/<имя> с settings.py
(find_sources). Для каждой записи таблицы читается
папка <папка результатов>/st4/<путь записи>_output_st4/ (как пишет st4) — на выбор уровень (LEVELS):
    tracks  <область>.mat: строка на траекторию в области — x, y, мм; vx, vy, |v|, м/с; r, мкм
            (p_in_area_scale) и высота и нижний край области, мм (area_hight_scale_mm, area_low_edge_hight_scale_mm);
    areas   _all_stat.dat: строка на область с траекториями — высота и нижний край, мм; число траекторий;
            средние vx, vy, м/с; r, мкм.
К каждой строке добавляются эксперимент, источник (Python / MATLAB), запись (rec_name) и все числовые колонки
таблицы записей (F, cond_num, bag_hight, …; если имя совпало с колонкой результатов — с приставкой table_);
их подписи — описания колонок из второй строки таблицы.
Всё чтение форматов результатов — здесь: если выходные файлы изменятся, править только этот файл.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from scipy.io import loadmat

from ..common import files
from ..common.jobs import load_module
from ..common.table_edit import EditableTable, is_empty

log = logging.getLogger(__name__)

LEVELS = {"tracks": "траектории в областях (st4)", "areas": "средние по областям (_all_stat.dat)"}
LABELS = {  # подписи колонок для осей и таблиц
    "experiment": "эксперимент", "source": "источник", "record": "запись",
    "x": "x, мм", "y": "y, мм", "vx": "vx, м/с", "vy": "vy, м/с", "v": "|v|, м/с", "r": "r, мкм",
    "area_height": "высота области, мм", "area_low_edge": "нижний край области, мм", "tracks": "траекторий в области",
}
TEXT_COLUMNS = ("experiment", "source", "record")
AREA_VARIABLES = ["p_in_area_scale", "area_hight_scale_mm", "area_low_edge_hight_scale_mm"]  # что читать из .mat


@dataclass(frozen=True)
class Source:
    """Результаты одного эксперимента: experiment — имя; origin — "Python" (OUTPUT_DIR) или "MATLAB"
    (MATLAB_RESULTS_DIR); table_file — таблица записей; results_dir — папка, в которой подпапка st4."""

    experiment: str
    origin: str
    table_file: Path
    results_dir: Path

    @property
    def label(self) -> str:
        """Подпись для списка: имя эксперимента (у результатов MATLAB — «эксперимент — результаты MATLAB»)."""
        return self.experiment if self.origin == "Python" else f"{self.experiment} — результаты {self.origin}"


def sources_of(experiment: str, settings: Any, matlab: bool = False) -> list[Source]:
    """Источники эксперимента по его settings: результаты Python (OUTPUT_DIR); matlab — ещё и результаты
    MATLAB (MATLAB_RESULTS_DIR), если он задан."""
    found = [Source(experiment, "Python", Path(settings.TABLE_FILE), Path(settings.OUTPUT_DIR))]
    matlab_dir = getattr(settings, "MATLAB_RESULTS_DIR", None)
    if matlab and matlab_dir:
        found.append(Source(experiment, "MATLAB", Path(settings.TABLE_FILE), Path(matlab_dir)))
    return found


def find_sources(experiments_dir: Path, matlab: bool = False) -> tuple[list[Source], list[str]]:
    """Источники всех экспериментов папки experiments_dir (папки с settings.py; matlab — с результатами MATLAB);
    второе — что не прочиталось."""
    found, problems = [], []
    for folder in sorted(p for p in Path(experiments_dir).iterdir() if (p / "settings.py").is_file()):
        try:
            settings = load_module(folder / "settings.py", f"analysis_settings_{folder.name}")
            found += sources_of(folder.name, settings, matlab)
        except Exception as error:  # ошибка в settings.py одного эксперимента не мешает остальным
            problems.append(f"{folder.name}: settings.py не прочитан: {error}")
    return found, problems


class DataTable:
    """Таблица для обработки результатов: колонки по именам, все одной длины; числа — float64, текст — object.
    columns — {имя: массив}; labels — подписи колонок (для осей), если отличаются от LABELS."""

    def __init__(self, columns: dict[str, np.ndarray] | None = None, labels: dict[str, str] | None = None) -> None:
        self.columns = dict(columns or {})
        self.labels = dict(labels or {})

    def __len__(self) -> int:
        return len(next(iter(self.columns.values()))) if self.columns else 0

    def label(self, name: str) -> str:
        """Подпись колонки для оси: из LABELS, иначе «имя — описание из таблицы записей», иначе имя."""
        if name in LABELS:
            return LABELS[name]
        return f"{name} — {self.labels[name]}" if self.labels.get(name) else name

    @property
    def names(self) -> list[str]:
        """Имена колонок по порядку."""
        return list(self.columns)

    def is_numeric(self, name: str) -> bool:
        """Числовая ли колонка."""
        return self.columns[name].dtype != object

    def take(self, mask: np.ndarray) -> "DataTable":
        """Строки, где mask истинно."""
        return DataTable({name: column[mask] for name, column in self.columns.items()}, self.labels)

    def unique(self, name: str) -> list[Any]:
        """Разные значения колонки по возрастанию (у чисел — без NaN)."""
        column = self.columns[name]
        if self.is_numeric(name):
            return [float(v) for v in np.unique(column[np.isfinite(column)])]
        return sorted({str(v) for v in column})

    @staticmethod
    def concat(tables: list["DataTable"]) -> "DataTable":
        """Таблицы друг под другом; колонки — объединение, недостающее — NaN или пустой текст."""
        names = list(dict.fromkeys(name for table in tables for name in table.names))
        text = {name for table in tables for name in table.names if not table.is_numeric(name)}
        columns = {}
        for name in names:
            kind = object if name in text else np.float64
            columns[name] = np.concatenate(
                [table.columns[name].astype(kind) if name in table.columns
                 else np.full(len(table), "" if name in text else np.nan, dtype=kind) for table in tables])
        labels = {}
        for table in tables:
            labels.update({name: text for name, text in table.labels.items() if text and name not in labels})
        return DataTable(columns, labels)


def _track_blocks(folder: Path, problems: list[str]) -> list[dict[str, np.ndarray]]:
    """Уровень tracks: по таблице на файл области <область>.mat (области без траекторий .mat не имеют)."""
    blocks = []
    for path in sorted(folder.glob("*.mat")):
        try:
            mat = loadmat(path, variable_names=AREA_VARIABLES)
            p = np.atleast_2d(np.asarray(mat["p_in_area_scale"], dtype=np.float64))
            height = float(np.ravel(mat["area_hight_scale_mm"])[0])
            low_edge = float(np.ravel(mat["area_low_edge_hight_scale_mm"])[0])
        except Exception as error:  # чужой или испорченный файл — пропустить и сказать
            problems.append(f"{path}: не прочитан: {error}")
            continue
        if p.size == 0 or p.shape[1] < 5:
            continue
        n = p.shape[0]
        blocks.append({"x": p[:, 0], "y": p[:, 1], "vx": p[:, 2], "vy": p[:, 3], "v": np.hypot(p[:, 2], p[:, 3]),
                       "r": p[:, 4], "area_height": np.full(n, height), "area_low_edge": np.full(n, low_edge)})
    return blocks


def _area_blocks(folder: Path, problems: list[str]) -> list[dict[str, np.ndarray]]:
    """Уровень areas: таблица из _all_stat.dat (строка на область с траекториями)."""
    path = folder / "_all_stat.dat"
    if not path.exists():
        return []
    try:
        stat = files.load_dat(path)
    except Exception as error:
        problems.append(f"{path}: не прочитан: {error}")
        return []
    if stat.size == 0 or stat.shape[1] < 6:
        return []
    return [{"area_height": stat[:, 0], "area_low_edge": stat[:, 1], "tracks": stat[:, 2], "vx": stat[:, 3],
             "vy": stat[:, 4], "v": np.hypot(stat[:, 3], stat[:, 4]), "r": stat[:, 5]}]


def load(sources: list[Source], level: str,
         progress: Callable[[int, int], None] | None = None) -> tuple[DataTable, list[str]]:
    """Строки уровня level ("tracks" или "areas") из результатов st4 источников sources. Возвращает таблицу
    и список того, что не прочиталось. progress(сделано, всего) вызывается после каждого источника."""
    read = _track_blocks if level == "tracks" else _area_blocks
    tables, problems = [], []
    for done, source in enumerate(sources, start=1):
        try:
            table = EditableTable(source.table_file)
        except Exception as error:
            problems.append(f"{source.label}: таблица записей не прочитана: {error}")
            continue
        numeric = [h for h in table.headers if table.is_numeric(h)]
        for i in range(len(table)):
            record = table.record(i)
            folder = record.st4_dir(source.results_dir) if record is not None else None
            if folder is None or not folder.is_dir():
                continue
            for block in read(folder, problems):
                n = len(next(iter(block.values())))
                columns = {"experiment": np.full(n, source.experiment, dtype=object),
                           "source": np.full(n, source.origin, dtype=object),
                           "record": np.full(n, record.name, dtype=object), **block}
                labels = {}
                for header in numeric:
                    value = table.value(i, header)
                    name = f"table_{header}" if header in columns or header in LABELS else header
                    columns[name] = np.full(n, np.nan if is_empty(value) else float(value))
                    labels[name] = table.descriptions.get(header, "")
                tables.append(DataTable(columns, labels))
        if progress is not None:
            progress(done, len(sources))
    log.info("Обработка результатов: %d строк из %d источников", sum(len(t) for t in tables), len(sources))
    return DataTable.concat(tables), problems
