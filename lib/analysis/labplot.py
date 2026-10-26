"""Обработка результатов: экспорт таблиц для LabPlot — CSV и проект LabPlot (.lml).

CSV: первая строка — имена колонок, разделитель «,», десятичная точка, UTF-8; LabPlot открывает его через
«Файл → Импорт → Данные из файла» (ASCII), Excel и другие программы — тоже.

Проект .lml — так, как его сохраняет LabPlot 2.9 (xmlVersion 7; более новые версии такие проекты читают):
XML, сжатый xz; <project> → <child_aspect> → <spreadsheet> (таблица) → <column> (колонка). Числовая колонка
(mode 0) хранит числа одним блоком base64 (double, little-endian) после своих фильтров, текстовая (mode 1) —
по элементу <row index="…"> на значение. Первая колонка таблицы назначена осью X, остальные — Y. Графики
в проект не пишутся: их строят уже в LabPlot по этим таблицам.
"""

import base64
import csv
import lzma
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import numpy as np

LABPLOT_VERSION, XML_VERSION = "2.9.0", "7"  # формат проекта LabPlot, который пишется


def write_csv(path: Path, columns: dict[str, np.ndarray]) -> None:
    """Таблица {имя колонки: значения} в CSV (числа — без потери точности, NaN — пустая ячейка)."""
    names = list(columns)
    n = len(next(iter(columns.values()))) if columns else 0
    with open(path, "w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(names)
        for i in range(n):
            row = []
            for name in names:
                value = columns[name][i]
                if isinstance(value, (float, np.floating)):
                    row.append("" if np.isnan(value) else repr(float(value)))
                else:
                    row.append(str(value))
            writer.writerow(row)


def _now() -> str:
    """Время в записи LabPlot: год-день-месяц часы:минуты:секунды:миллисекунды."""
    now = datetime.now()
    return now.strftime("%Y-%d-%m %H:%M:%S:") + f"{now.microsecond // 1000:03d}"


def _filters(column: ET.Element, numeric: bool) -> None:
    """Фильтры ввода и вывода колонки (как у LabPlot для чисел и для текста)."""
    for tag, name, filter_name, extra in (
            ("input_filter", "SimpleFilter 1", "String2DoubleFilter" if numeric else "SimpleCopyThroughFilter", {}),
            ("output_filter", "SimpleFilter", "Double2StringFilter" if numeric else "SimpleCopyThroughFilter",
             {"format": "g", "digits": "6"} if numeric else {})):
        holder = ET.SubElement(column, tag)
        simple = ET.SubElement(holder, "simple_filter", {"creation_time": _now(), "name": name, **extra,
                                                         "filter_name": filter_name})
        ET.SubElement(simple, "comment").text = ""


def _column(spreadsheet: ET.Element, name: str, values: np.ndarray, first: bool) -> None:
    """Колонка таблицы: числа — base64 (double, little-endian), иначе текст по строкам."""
    numeric = values.dtype != object
    column = ET.SubElement(spreadsheet, "column", {
        "creation_time": _now(), "name": name, "rows": str(len(values)), "designation": "1" if first else "2",
        "mode": "0" if numeric else "1", "width": "120"})
    ET.SubElement(column, "comment").text = ""
    _filters(column, numeric)
    if numeric:
        data = np.asarray(values, dtype="<f8").tobytes()
        column[-1].tail = base64.b64encode(data).decode("ascii")  # данные — текст после фильтров, как у LabPlot
    else:
        for i, value in enumerate(values):
            ET.SubElement(column, "row", {"index": str(i)}).text = str(value)


def write_lml(path: Path, spreadsheets: dict[str, dict[str, np.ndarray]], author: str = "") -> None:
    """Проект LabPlot с таблицами {имя таблицы: {имя колонки: значения}}, сжатый xz."""
    project = ET.Element("project", {"version": LABPLOT_VERSION, "xmlVersion": XML_VERSION,
                                     "modificationTime": _now(), "author": author, "saveCalculations": "1",
                                     "creation_time": _now(), "name": "Project"})
    ET.SubElement(project, "comment").text = ""
    for sheet_name, columns in spreadsheets.items():
        holder = ET.SubElement(project, "child_aspect")
        sheet = ET.SubElement(holder, "spreadsheet", {"creation_time": _now(),
                                                      "name": sheet_name.replace("/", "-")})
        ET.SubElement(sheet, "comment").text = ""
        for k, (name, values) in enumerate(columns.items()):
            _column(sheet, name.replace("/", "-"), np.asarray(values), k == 0)
    ET.indent(project, space="    ")
    text = '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE LabPlotXML>\n' + ET.tostring(project, encoding="unicode")
    Path(path).write_bytes(lzma.compress(text.encode("utf-8"), format=lzma.FORMAT_XZ))
