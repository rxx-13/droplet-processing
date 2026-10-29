"""Часть «Обработка результатов»: распределения и зависимости по итоговым файлам st4 многих экспериментов.

Не привязана к текущему эксперименту. Раскладка — как в LabPlot: слева данные, в середине график, справа его
свойства; границы двигаются мышью. Разделы пронумерованы по порядку работы, щелчок по заголовку сворачивает и
разворачивает раздел.
Слева:
    1 Данные         эксперименты из папки experiments/ (их результаты Python) — отметить (щелчок по строке, можно
                     протянуть мышью); уровень: «траектории в областях» (строка на траекторию в области st4: x, y, мм;
                     vx, vy, |v|, м/с; r, мкм; высота и нижний край области, мм) или «средние по областям»
                     (_all_stat.dat); к строкам добавляются параметры записи из таблицы записей (F, cond_num,
                     bag_hight, …). «Загрузить» — прочитать; после загрузки у эксперимента — сколько записей и строк.
    2 Свои колонки   строки «имя = формула», например «U = 0.07*F» — новые колонки для осей и выборки.
    3 Выборка        условия: колонка «равна» выбранным значениям (например, F = 140) или лежит «от–до»; действуют
                     все условия сразу; у каждого — сколько строк остаётся после него.
В середине — сводка (что построено, выборка, разделение, сколько кривых), график (увеличение, сдвиг, сохранение
PNG/SVG/PDF — панель под ним), ниже — таблица посчитанного.
Справа:
    4 Что строить    распределение (гистограмма) X; зависимость Y от X по окнам X (среднее ± СКО, ± ошибка среднего,
                     медиана с квартилями, число); точки. Окна — ширина или число, пределы «от–до».
    5 Разделение     цвет по колонке (своя кривая на каждое значение, например F), маркер и линия — по другой
                     (например, эксперимент), графики рядом — по третьей (общие оси; не больше MAX_PANELS).
    6 Кривые         список кривых: показать / скрыть, цвет, маркер, линия, подпись в легенде, строк; правки
                     помнятся по подписи кривой и сохраняются в файл настроек.
    7 Своя кривая    функция f(x) с параметрами, например «a*x**b + c»: нарисовать с заданными значениями или
                     подогнать параметры к каждой кривой (наименьшие квадраты; параметры ± погрешность, R²).
    8 Оси и подписи  заголовок, подписи и пределы осей, логарифмические оси, сетка.
    9 Оформление     легенда, цвета, шрифт, толщина линий, размер точек, усы разброса.
    10 Экспорт       выборка и посчитанное — в CSV или в проект LabPlot (.lml); все настройки этой части —
                     сохранить в файл и загрузить (повторить картинку позже).
Любая правка через короткую паузу перестраивает картинку. Расчёты — lib/analysis/.
"""

import json
import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import matplotlib
import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.colors import to_hex
from matplotlib.figure import Figure
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QColorDialog, QComboBox, QFileDialog,
                               QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSplitter,
                               QTableWidget, QTableWidgetItem, QToolButton, QVBoxLayout, QWidget)

from app.jobs_page import CheckPainter
from lib.analysis.dataset import LEVELS, DataTable, Source, find_sources, load
from lib.analysis.formulas import FitResult, computed_columns, evaluate, fit, parameters_of
from lib.analysis.labplot import write_csv, write_lml
from lib.analysis.stats import (NORMS, STATISTICS, Condition, Series, binned, histogram, label_value, points,
                                split, window_edges)

log = logging.getLogger(__name__)

KINDS = {"binned": "зависимость Y от X по окнам X", "histogram": "распределение (гистограмма) X",
         "points": "точки (X, Y)"}
CURVE_MODES = {"off": "не показывать", "draw": "нарисовать с этими значениями", "fit": "подогнать к каждой кривой"}
LEGENDS = {"best": "сама", "upper right": "справа вверху", "upper left": "слева вверху",
           "lower right": "справа внизу", "lower left": "слева внизу", "none": "не показывать"}
PALETTES = {"tab10": "обычные", "Set1": "яркие", "Dark2": "тёмные", "viridis": "синий → жёлтый",
            "plasma": "синий → оранжевый", "cividis": "для печати"}
MARKERS = {"o": ("●", "круг"), "s": ("■", "квадрат"), "^": ("▲", "треугольник"), "D": ("◆", "ромб"),
           "v": ("▼", "треугольник вниз"), "P": ("✚", "крест"), "X": ("✖", "косой крест"), "None": ("нет", "без маркера")}
LINES = {"-": ("——", "сплошная"), "--": ("– –", "штрихи"), ":": ("···", "точки"), "-.": ("–·", "штрих-точка"),
         "None": ("нет", "без линии")}  # в списке кривых: значок и подсказка
MARKER_CYCLE = ["o", "s", "^", "D", "v", "P", "X"]  # «маркер по»: маркеры значений по порядку
LINE_CYCLE = ["-", "--", ":", "-."]  # «маркер по»: линии значений по порядку
AUTO = "авто"
MAX_VALUES = 300  # «равно»: список значений колонки показывается, если разных значений не больше
MAX_SERIES_VALUES = 40  # «цвет / маркер / графики рядом по»: колонки, у которых разных значений не больше
MAX_PANELS = 12  # графиков рядом не больше
MAX_CURVES = 120  # кривых на картинке не больше
MAX_TABLE_ROWS = 3000  # строк в таблице посчитанного
REPLOT_MS = 200  # пауза перед перестройкой картинки после правки
CURVE_POINTS = 300  # точек на своей кривой


def _number(text: str) -> float | None:
    """Число из поля ввода (запятая тоже годится); пусто или не число — None."""
    try:
        return float(text.strip().replace(",", "."))
    except ValueError:
        return None


def _count(n: int) -> str:
    """Число строк с пробелами между тысячами: 29 734."""
    return f"{n:,}".replace(",", " ")


def _describe(condition: Condition) -> str:
    """Условие выборки словами: «F = 40, 50» или «r 100…500»."""
    if condition.values:
        shown = ", ".join(label_value(v) for v in condition.values[:6])
        return f"{condition.column} = {shown}{', …' if len(condition.values) > 6 else ''}"
    low = "" if condition.low is None else f"{condition.low:g}"
    high = "" if condition.high is None else f"{condition.high:g}"
    return f"{condition.column} {low}…{high}"


@dataclass
class Drawn:
    """Кривая графика: panel — подпись графика, на котором она («» — график один); key — подпись кривой без
    графика (по ней помнится её вид в списке кривых); series — посчитанное; color, marker — номера значений
    «цвет по» и «маркер по» (вид по умолчанию); rows — строк выборки в кривой."""

    panel: str
    key: str
    series: Series
    color: int
    marker: int
    rows: int

    @property
    def name(self) -> str:
        """Полная подпись для таблицы и экспорта: «график | кривая»."""
        return " | ".join(part for part in (self.panel, self.key) if part)


class Section(QWidget):
    """Раздел боковой панели: заголовок-кнопка со стрелкой (щелчок — свернуть / развернуть) и содержимое.
    title — заголовок; expanded — развёрнут ли сначала. Содержимое кладётся в self.body (QVBoxLayout)."""

    def __init__(self, title: str, expanded: bool = True) -> None:
        super().__init__()
        self.button = QToolButton()
        self.button.setText(title)
        self.button.setCheckable(True)
        self.button.setChecked(expanded)
        self.button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.button.setStyleSheet("QToolButton { border: none; font-weight: bold; }")
        self.content = QWidget()
        self.body = QVBoxLayout(self.content)
        self.body.setContentsMargins(12, 0, 0, 6)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self.button)
        layout.addWidget(self.content)
        self.button.toggled.connect(self._toggle)
        self._toggle(expanded)

    def _toggle(self, expanded: bool) -> None:
        """Свернуть или развернуть содержимое."""
        self.button.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        self.content.setVisible(expanded)


class ConditionRow(QWidget):
    """Условие выборки: колонка; «равно» (значения выбираются в списке, можно несколько) или «от–до»;
    «×» — убрать условие; под условием — сколько строк остаётся после него. table — загруженные данные."""

    changed = Signal()
    removed = Signal(object)

    def __init__(self, table: DataTable) -> None:
        super().__init__()
        self.table = table
        self.column = QComboBox()
        self.mode = QComboBox()
        self.mode.addItems(["равно", "от–до"])
        self.values = QListWidget()
        self.values.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        self.values.setMaximumHeight(90)
        self.low, self.high = QLineEdit(), QLineEdit()
        self.count = QLabel()
        self.count.setStyleSheet("color: #606060;")
        remove = QToolButton()
        remove.setText("×")
        remove.setToolTip("убрать условие")
        remove.clicked.connect(lambda: self.removed.emit(self))
        top = QHBoxLayout()
        top.addWidget(self.column, 1)
        top.addWidget(self.mode)
        top.addWidget(remove)
        self.range_row = QWidget()
        bounds = QHBoxLayout(self.range_row)
        bounds.setContentsMargins(0, 0, 0, 0)
        bounds.addWidget(QLabel("от"))
        bounds.addWidget(self.low)
        bounds.addWidget(QLabel("до"))
        bounds.addWidget(self.high)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 6)
        layout.addLayout(top)
        layout.addWidget(self.values)
        layout.addWidget(self.range_row)
        layout.addWidget(self.count)
        self.column.currentIndexChanged.connect(lambda _: self._column_changed())
        self.mode.currentIndexChanged.connect(lambda _: self._mode_changed())
        self.values.itemSelectionChanged.connect(self.changed.emit)
        self.low.textChanged.connect(lambda _: self.changed.emit())
        self.high.textChanged.connect(lambda _: self.changed.emit())
        self.set_table(table)

    def set_table(self, table: DataTable) -> None:
        """Новые данные: список колонок (выбранная колонка и выбранные значения остаются, если есть)."""
        self.table = table
        old, chosen = self.column.currentText(), self.state()["values"]
        self.column.blockSignals(True)
        self.column.clear()
        self.column.addItems(table.names)
        self.column.setCurrentIndex(max(self.column.findText(old), 0))
        self.column.blockSignals(False)
        self._column_changed()
        if self.column.currentText() == old:
            self._select(chosen)

    def _column_changed(self) -> None:
        """Выбрана колонка: значения для «равно», подсказки границ для «от–до»."""
        name = self.column.currentText()
        self.values.blockSignals(True)
        self.values.clear()
        if name:
            unique = self.table.unique(name)
            if len(unique) <= MAX_VALUES:
                for value in unique:  # в строке — точное значение (подпись округлена)
                    item = QListWidgetItem(label_value(value))
                    item.setData(Qt.ItemDataRole.UserRole, value)
                    self.values.addItem(item)
            numeric = self.table.is_numeric(name)
            self.mode.model().item(1).setEnabled(numeric)  # «от–до» — только для чисел
            if not numeric:
                self.mode.setCurrentIndex(0)
            elif len(unique) > MAX_VALUES:
                self.mode.setCurrentIndex(1)
            if numeric and unique:
                self.low.setPlaceholderText(f"{unique[0]:g}")
                self.high.setPlaceholderText(f"{unique[-1]:g}")
        self.values.blockSignals(False)
        self._mode_changed()

    def _mode_changed(self) -> None:
        """«равно» — список значений, «от–до» — два поля."""
        ranged = self.mode.currentIndex() == 1
        self.values.setVisible(not ranged)
        self.range_row.setVisible(ranged)
        self.changed.emit()

    def condition(self) -> Condition | None:
        """Условие по полям (None — колонка не выбрана или ничего не задано)."""
        name = self.column.currentText()
        if not name:
            return None
        if self.mode.currentIndex() == 1:
            low, high = _number(self.low.text()), _number(self.high.text())
            return Condition(name, low=low, high=high) if low is not None or high is not None else None
        chosen = tuple(item.data(Qt.ItemDataRole.UserRole) for item in self.values.selectedItems())
        return Condition(name, chosen) if chosen else None

    def set_count(self, rows: int | None) -> None:
        """Подпись под условием: сколько строк остаётся после него (None — условие пока ничего не задаёт)."""
        self.count.setText("условие не задано — выберите значения или границы" if rows is None
                           else f"→ остаётся строк: {_count(rows)}")

    def _select(self, values: list[Any]) -> None:
        """Выбрать в списке «равно» строки с этими значениями."""
        for i in range(self.values.count()):
            item = self.values.item(i)
            item.setSelected(item.data(Qt.ItemDataRole.UserRole) in values)

    def select_values(self, texts: list[str]) -> None:
        """Выбрать значения по подписям (как если бы их отметили мышью)."""
        self.mode.setCurrentIndex(0)
        for i in range(self.values.count()):
            item = self.values.item(i)
            item.setSelected(item.text() in texts)

    def state(self) -> dict[str, Any]:
        """Настройки условия (для сохранения в файл)."""
        return {"column": self.column.currentText(), "mode": self.mode.currentIndex(),
                "values": [item.data(Qt.ItemDataRole.UserRole) for item in self.values.selectedItems()],
                "low": self.low.text(), "high": self.high.text()}

    def set_state(self, state: dict[str, Any]) -> None:
        """Восстановить настройки условия."""
        self.column.setCurrentIndex(max(self.column.findText(state.get("column", "")), 0))
        self.mode.setCurrentIndex(int(state.get("mode", 0)))
        self._select(list(state.get("values", [])))
        self.low.setText(state.get("low", ""))
        self.high.setText(state.get("high", ""))


class AnalysisPage(QWidget):
    """Часть «Обработка результатов». experiments_dir — папка экспериментов (у каждого свой settings.py)."""

    message = Signal(str)  # сообщение для строки состояния

    def __init__(self, experiments_dir: Path) -> None:
        super().__init__()
        self.experiments_dir = Path(experiments_dir)
        self.sources: list[Source] = []
        self.source_counts: dict[str, tuple[int, int]] = {}  # эксперимент -> (записей, строк) после загрузки
        self.loaded = DataTable()  # прочитанное из файлов
        self.data = DataTable()  # + свои колонки
        self.selection = DataTable()
        self.drawn: list[Drawn] = []  # все кривые графика, и скрытые
        self.visible: list[Drawn] = []  # показанные
        self.series: list[Series] = []  # посчитанное у показанных кривых
        self.curves: list[Series] = []  # свои кривые (нарисованные или подогнанные)
        self.fits: list[tuple[str, FitResult]] = []
        self.styles: dict[str, dict[str, Any]] = {}  # правки вида кривых: подпись кривой -> {visible, color, …}
        self.curve_keys: list[str] = []  # кривые в списке «Кривые»
        self.colors: list = []  # цвета значений «цвет по» на текущей картинке
        self.rows: list[ConditionRow] = []
        self.last_dir = str(Path.home())
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(REPLOT_MS)
        self.timer.timeout.connect(self.replot)
        self._build()
        self.refresh_sources()
        self.replot()

    # ------------------------------------------------------------------ разметка

    def _combo(self, items: dict[str, str]) -> QComboBox:
        """Выпадающий список {ключ: подпись}; выбор — перестроить картинку."""
        box = QComboBox()
        for key, text in items.items():
            box.addItem(text, key)
        box.currentIndexChanged.connect(lambda _: self.timer.start())
        return box

    def _edit(self, placeholder: str) -> QLineEdit:
        """Поле ввода с подсказкой; правка — перестроить картинку."""
        line = QLineEdit()
        line.setPlaceholderText(placeholder)
        line.textChanged.connect(lambda _: self.timer.start())
        return line

    def _check(self, text: str, checked: bool = False) -> QCheckBox:
        """Флажок; щелчок — перестроить картинку."""
        box = QCheckBox(text)
        box.setChecked(checked)
        box.toggled.connect(lambda _: self.timer.start())
        return box

    @staticmethod
    def _panel(sections: list[Section], width: int) -> QScrollArea:
        """Боковая панель: разделы друг под другом, с прокруткой."""
        panel = QWidget()
        column = QVBoxLayout(panel)
        for section in sections:
            column.addWidget(section)
        column.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(panel)
        scroll.setMinimumWidth(width)
        return scroll

    def _build(self) -> None:
        """Как в LabPlot: слева данные и выборка, в середине сводка, график и таблица, справа свойства графика."""
        left = self._panel([self._data_section(), self._columns_section(), self._selection_section()], 300)
        right = self._panel([self._plot_section(), self._split_section(), self._curves_section(),
                             self._curve_section(), self._axes_section(), self._view_section(),
                             self._export_section()], 380)

        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.summary.setStyleSheet("QLabel { background: #f3f6f9; border: 1px solid #d0d7de; padding: 4px; }")
        self.figure = Figure(figsize=(8, 6), layout="constrained")
        self.canvas = FigureCanvasQTAgg(self.figure)
        plot = QWidget()
        column = QVBoxLayout(plot)
        column.setContentsMargins(0, 0, 0, 0)
        column.addWidget(self.summary)
        column.addWidget(self.canvas, 1)
        column.addWidget(NavigationToolbar2QT(self.canvas, plot))
        self.values = QTableWidget(0, 6)
        self.values.setHorizontalHeaderLabels(["кривая", "x", "y", "разброс от", "до", "число"])
        self.values.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.values.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.values.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        middle = QSplitter(Qt.Orientation.Vertical)
        middle.addWidget(plot)
        middle.addWidget(self.values)
        middle.setSizes([650, 200])
        splitter = QSplitter(Qt.Orientation.Horizontal)
        for widget in (left, middle, right):
            splitter.addWidget(widget)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([300, 880, 420])
        splitter.setChildrenCollapsible(False)
        layout = QVBoxLayout(self)
        layout.addWidget(splitter)

    def _data_section(self) -> Section:
        """Раздел «1 Данные»: эксперименты, уровень, «Загрузить»."""
        section = Section("1 Данные")
        self.source_list = QListWidget()
        self.source_painter = CheckPainter(self.source_list)  # щелчок по строке, протягивание, Shift
        self.source_list.setMaximumHeight(140)
        self.level = QComboBox()
        for key, text in LEVELS.items():
            self.level.addItem(text, key)
        load_button = QPushButton("Загрузить")
        load_button.clicked.connect(self.load_data)
        refresh = QPushButton("Обновить список")
        refresh.setToolTip("перечитать список экспериментов (папки experiments/)")
        refresh.clicked.connect(self.refresh_sources)
        self.data_info = QLabel("Отметьте эксперименты и нажмите «Загрузить»")
        self.data_info.setWordWrap(True)
        buttons = QHBoxLayout()
        buttons.addWidget(load_button)
        buttons.addWidget(refresh)
        for widget in (self.source_list, self.level):
            section.body.addWidget(widget)
        section.body.addLayout(buttons)
        section.body.addWidget(self.data_info)
        return section

    def _columns_section(self) -> Section:
        """Раздел «2 Свои колонки»: строки «имя = формула»."""
        section = Section("2 Свои колонки")
        self.columns_text = QPlainTextEdit()
        self.columns_text.setPlaceholderText("по строке «имя = формула», например:\nU = 0.07*F\nd = 2*r")
        self.columns_text.setMaximumHeight(70)
        self.columns_text.setToolTip("Формулы — по числовым колонкам данных; функции: sqrt, exp, log, sin, cos, abs, …")
        self.columns_text.textChanged.connect(self.timer.start)
        self.columns_info = QLabel()
        self.columns_info.setWordWrap(True)
        section.body.addWidget(self.columns_text)
        section.body.addWidget(self.columns_info)
        return section

    def _selection_section(self) -> Section:
        """Раздел «3 Выборка»: условия."""
        section = Section("3 Выборка")
        self.conditions = QVBoxLayout()
        add = QPushButton("+ условие")
        add.setToolTip("колонка равна выбранным значениям или лежит от–до; действуют все условия сразу")
        add.clicked.connect(lambda: self.add_condition())
        self.selection_info = QLabel()
        self.selection_info.setWordWrap(True)
        section.body.addLayout(self.conditions)
        section.body.addWidget(add)
        section.body.addWidget(self.selection_info)
        return section

    def _plot_section(self) -> Section:
        """Раздел «4 Что строить»: вид, оси, окна, статистика."""
        section = Section("4 Что строить")
        self.kind = self._combo(KINDS)
        self.x_column, self.y_column = QComboBox(), QComboBox()
        for box in (self.x_column, self.y_column):
            box.currentIndexChanged.connect(lambda _: self.timer.start())
        self.window_width, self.window_count = self._edit("ширина"), self._edit("или число (20)")
        self.window_low, self.window_high = self._edit("от: мин."), self._edit("до: макс.")
        self.statistic = self._combo(STATISTICS)
        self.norm = self._combo(NORMS)
        windows, bounds = QHBoxLayout(), QHBoxLayout()
        windows.addWidget(self.window_width)
        windows.addWidget(self.window_count)
        bounds.addWidget(self.window_low)
        bounds.addWidget(self.window_high)
        form = QFormLayout()
        form.addRow("вид", self.kind)
        form.addRow("X", self.x_column)
        form.addRow("Y", self.y_column)
        form.addRow("окна X", windows)
        form.addRow("пределы окон", bounds)
        form.addRow("по окнам", self.statistic)
        form.addRow("нормировка", self.norm)
        section.body.addLayout(form)
        return section

    def _split_section(self) -> Section:
        """Раздел «5 Разделение»: цвет, маркер и линия, графики рядом — по колонкам."""
        section = Section("5 Разделение")
        self.color_by, self.marker_by, self.facet_by = QComboBox(), QComboBox(), QComboBox()
        for box, tip in ((self.color_by, "своя кривая и свой цвет на каждое значение колонки, например F"),
                         (self.marker_by, "своя кривая, маркер и линия на каждое значение другой колонки, "
                                          "например эксперимент"),
                         (self.facet_by, f"отдельный график на каждое значение колонки, оси общие "
                                         f"(не больше {MAX_PANELS})")):
            box.setToolTip(tip)
            box.currentIndexChanged.connect(lambda _: self.timer.start())
        form = QFormLayout()
        form.addRow("цвет по", self.color_by)
        form.addRow("маркер, линия по", self.marker_by)
        form.addRow("графики рядом по", self.facet_by)
        section.body.addLayout(form)
        return section

    def _curves_section(self) -> Section:
        """Раздел «6 Кривые»: список кривых графика с их видом."""
        section = Section("6 Кривые")
        self.curve_table = QTableWidget(0, 6)
        self.curve_table.setHorizontalHeaderLabels(["", "цвет", "маркер", "линия", "подпись", "строк"])
        self.curve_table.verticalHeader().setVisible(False)
        header = self.curve_table.horizontalHeader()
        for k in range(6):
            header.setSectionResizeMode(k, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.curve_table.setMinimumHeight(160)
        self.curve_table.setMaximumHeight(320)
        self.curve_table.setToolTip("флажок — показать кривую; цвет — щелчок; подпись — двойной щелчок")
        self.curve_table.itemChanged.connect(self._curve_item_changed)
        reset = QPushButton("Сбросить вид кривых")
        reset.setToolTip("вернуть всем кривым цвет, маркер, линию и подпись по умолчанию и показать все")
        reset.clicked.connect(self.reset_styles)
        section.body.addWidget(self.curve_table)
        section.body.addWidget(reset)
        return section

    def _curve_section(self) -> Section:
        """Раздел «7 Своя кривая»: формула f(x), параметры, режим, результат."""
        section = Section("7 Своя кривая и подгонка")
        self.curve_formula = self._edit("f(x), например: a*x**b + c")
        self.curve_formula.setToolTip("Функция от x с параметрами; функции: sqrt, exp, log, sin, cos, abs, erf, …")
        self.curve_formula.textChanged.connect(lambda _: self._fill_parameters())
        self.curve_mode = self._combo(CURVE_MODES)
        self.parameters = QTableWidget(0, 2)
        self.parameters.setHorizontalHeaderLabels(["параметр", "начальное значение"])
        self.parameters.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.parameters.verticalHeader().setVisible(False)
        self.parameters.setMaximumHeight(110)
        self.parameters.itemChanged.connect(lambda _: self.timer.start())
        self.fit_info = QLabel()
        self.fit_info.setWordWrap(True)
        self.fit_info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        for widget in (self.curve_formula, self.curve_mode, self.parameters, self.fit_info):
            section.body.addWidget(widget)
        return section

    def _axes_section(self) -> Section:
        """Раздел «8 Оси и подписи»: заголовок, подписи, пределы, лог-оси, сетка."""
        section = Section("8 Оси и подписи")
        self.title = self._edit("заголовок: авто")
        self.x_label, self.y_label = self._edit("подпись X: авто"), self._edit("подпись Y: авто")
        self.x_from, self.x_to = self._edit("X от"), self._edit("X до")
        self.y_from, self.y_to = self._edit("Y от"), self._edit("Y до")
        self.log_x, self.log_y = self._check("лог. X"), self._check("лог. Y")
        self.grid = self._check("сетка", True)
        form = QFormLayout()
        form.addRow("заголовок", self.title)
        form.addRow("подпись X", self.x_label)
        form.addRow("подпись Y", self.y_label)
        for label, a, b in (("пределы X", self.x_from, self.x_to), ("пределы Y", self.y_from, self.y_to),
                            ("оси", self.log_x, self.log_y)):
            row = QHBoxLayout()
            row.addWidget(a)
            row.addWidget(b)
            form.addRow(label, row)
        form.addRow("", self.grid)
        section.body.addLayout(form)
        return section

    def _view_section(self) -> Section:
        """Раздел «9 Оформление»: легенда, цвета, шрифт, линии, точки, усы разброса."""
        section = Section("9 Оформление")
        self.legend = self._combo(LEGENDS)
        self.palette = self._combo(PALETTES)
        self.error_bars = self._check("разброс (усы)", True)
        self.font_size, self.line_width, self.marker_size = self._edit("10"), self._edit("1.2"), self._edit("4")
        form = QFormLayout()
        form.addRow("легенда", self.legend)
        form.addRow("цвета", self.palette)
        form.addRow("", self.error_bars)
        sizes = QHBoxLayout()
        for text, widget in (("шрифт", self.font_size), ("линии", self.line_width), ("точки", self.marker_size)):
            sizes.addWidget(QLabel(text))
            sizes.addWidget(widget)
        form.addRow(sizes)
        section.body.addLayout(form)
        return section

    def _export_section(self) -> Section:
        """Раздел «10 Экспорт и настройки»."""
        section = Section("10 Экспорт и настройки")
        for text, slot, tip in (
                ("Выборка → CSV", lambda: self._export_dialog("selection"), "все строки выборки, все колонки"),
                ("Посчитанное → CSV", lambda: self._export_dialog("series"),
                 "показанные кривые графика: окна, значения, разброс, число; свои кривые"),
                ("Всё → проект LabPlot (.lml)", lambda: self._export_dialog("labplot"),
                 "проект LabPlot: таблица выборки, по таблице на каждую кривую, параметры подгонки"),
                ("Сохранить настройки…", self._save_state_dialog, "все настройки этой части — в файл .json"),
                ("Загрузить настройки…", self._load_state_dialog, "загрузить настройки из файла .json")):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            section.body.addWidget(b)
        return section

    # ------------------------------------------------------------------ данные и выборка

    def refresh_sources(self) -> None:
        """Список источников: эксперименты папки experiments/ (отмеченные галочки остаются); у загруженных —
        сколько записей и строк."""
        checked = {s.label for s in self.checked_sources()}
        self.sources, problems = find_sources(self.experiments_dir) if self.experiments_dir.is_dir() else ([], [])
        self.source_list.clear()
        for source in self.sources:
            counts = self.source_counts.get(source.experiment)
            text = source.label if counts is None else f"{source.label}  ·  {counts[0]} зап., {_count(counts[1])} стр."
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, source.label)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if source.label in checked else Qt.CheckState.Unchecked)
            self.source_list.addItem(item)
        if problems:
            self.message.emit("; ".join(problems))

    def checked_sources(self) -> list[Source]:
        """Отмеченные источники."""
        return [s for k, s in enumerate(self.sources)
                if k < self.source_list.count() and self.source_list.item(k).checkState() == Qt.CheckState.Checked]

    def set_checked(self, labels: list[str]) -> None:
        """Отметить источники с подписями labels, остальные — снять."""
        for k in range(self.source_list.count()):
            item = self.source_list.item(k)
            label = item.data(Qt.ItemDataRole.UserRole)
            item.setCheckState(Qt.CheckState.Checked if label in labels else Qt.CheckState.Unchecked)

    def load_data(self) -> None:
        """«Загрузить»: прочитать итоговые файлы st4 отмеченных источников выбранного уровня."""
        sources = self.checked_sources()
        if not sources:
            self.message.emit("Отметьте хотя бы один эксперимент")
            return
        QApplication.setOverrideCursor(QCursor(Qt.CursorShape.WaitCursor))
        try:
            self.loaded, problems = load(sources, self.level.currentData(),
                                         progress=lambda done, total: QApplication.processEvents())
        finally:
            QApplication.restoreOverrideCursor()
        experiments = self.loaded.columns.get("experiment", np.array([], dtype=object))
        records = self.loaded.columns.get("record", np.array([], dtype=object))
        self.source_counts = {source.experiment: (len(set(records[experiments == source.experiment])),
                                                  int(np.sum(experiments == source.experiment)))
                              for source in sources}
        self.refresh_sources()
        self.data_info.setText(f"Загружено строк: {_count(len(self.loaded))}, записей: "
                               f"{sum(n for n, _ in self.source_counts.values())}"
                               + (f"; не прочитано файлов: {len(problems)} (подробно — в консоли)" if problems else ""))
        for problem in problems:
            log.warning(problem)
        self._apply_columns(force=True)
        self.replot()

    def _apply_columns(self, force: bool = False) -> None:
        """Свои колонки: пересчитать по формулам; если набор колонок изменился — обновить списки колонок."""
        old = self.data.names
        if not len(self.loaded):  # данных ещё нет — считать нечего
            self.data = self.loaded
            self.columns_info.setText("")
            return
        self.data, errors = computed_columns(self.loaded, self.columns_text.toPlainText())
        self.columns_info.setText("\n".join(errors))
        self.columns_info.setStyleSheet("color: #c00000;" if errors else "")
        if force or self.data.names != old:
            self._fill_columns()
            for row in self.rows:
                row.set_table(self.data)

    def _fill_columns(self) -> None:
        """Списки колонок X, Y и разделения — по данным (выбранное остаётся, если есть)."""
        numeric = [n for n in self.data.names if self.data.is_numeric(n)]
        few = [n for n in self.data.names if len(self.data.unique(n)) <= MAX_SERIES_VALUES]
        for box, names, default in ((self.x_column, numeric, "r"), (self.y_column, numeric, "v"),
                                    (self.color_by, ["", *few], ""), (self.marker_by, ["", *few], ""),
                                    (self.facet_by, ["", *few], "")):
            old = box.currentData()
            box.blockSignals(True)
            box.clear()
            for name in names:
                box.addItem(self.data.label(name) if name else "— нет", name)
            box.setCurrentIndex(max(box.findData(old if old in names else default), 0))
            box.blockSignals(False)

    def add_condition(self) -> ConditionRow:
        """«+ условие»: новая строка условия выборки."""
        row = ConditionRow(self.data)
        row.changed.connect(self.timer.start)
        row.removed.connect(self._remove_condition)
        self.conditions.addWidget(row)
        self.rows.append(row)
        return row

    def _remove_condition(self, row: ConditionRow) -> None:
        """«×»: убрать условие."""
        self.rows.remove(row)
        row.deleteLater()
        self.timer.start()

    def _select(self) -> list[Condition]:
        """Выборка по условиям (self.selection); у каждого условия — сколько строк остаётся после него.
        Возвращает действующие условия."""
        keep, conditions = np.ones(len(self.data), dtype=bool), []
        for row in self.rows:
            condition = row.condition()
            if condition is None:
                row.set_count(None)
                continue
            conditions.append(condition)
            keep &= condition.mask(self.data)
            row.set_count(int(keep.sum()))
        self.selection = self.data.take(keep)
        return conditions

    # ------------------------------------------------------------------ кривые: разделение и вид

    def _split(self, kind: str, x_name: str, y_name: str, edges: np.ndarray) -> list[Drawn]:
        """Кривые выборки: по графикам рядом, затем по цвету, затем по маркеру; у каждой — номера значений
        цвета и маркера (по всей выборке, чтобы на всех графиках одно значение было одного вида)."""
        color_by, marker_by, facet_by = (self.color_by.currentData() or None, self.marker_by.currentData() or None,
                                         self.facet_by.currentData() or None)
        if marker_by == color_by:
            marker_by = None
        color_index = {label: k for k, (label, _) in enumerate(split(self.selection, color_by))}
        marker_index = {label: k for k, (label, _) in enumerate(split(self.selection, marker_by))}
        drawn = []
        for panel, panel_rows in split(self.selection, facet_by):
            for color_label, color_rows in split(panel_rows, color_by):
                for marker_label, part in split(color_rows, marker_by):
                    key = "; ".join(t for t in (color_label, marker_label) if t)
                    if kind == "histogram":
                        series = histogram(part.columns[x_name], edges, self.norm.currentData(), key)
                    elif kind == "binned":
                        series = binned(part.columns[x_name], part.columns[y_name], edges,
                                        self.statistic.currentData(), key)
                    else:
                        series = points(part.columns[x_name], part.columns[y_name], key)
                    drawn.append(Drawn(panel, key, series, color_index[color_label], marker_index[marker_label],
                                       len(part)))
        return drawn

    def _look(self, d: Drawn) -> dict[str, Any]:
        """Вид кривой: по умолчанию (цвет — по значению «цвет по», маркер и линия — по значению «маркер по»)
        и поверх — правки из списка кривых."""
        styled = self.marker_by.currentData() and self.marker_by.currentData() != self.color_by.currentData()
        colors = self.colors or self._colors(1)
        look = {"visible": True, "color": to_hex(colors[d.color % len(colors)]),
                "marker": MARKER_CYCLE[d.marker % len(MARKER_CYCLE)] if styled else "o",
                "line": LINE_CYCLE[d.marker % len(LINE_CYCLE)] if styled else "-", "label": d.key}
        look.update(self.styles.get(d.key, {}))
        return look

    def set_style(self, key: str, **values: Any) -> None:
        """Правка вида кривой key (visible, color, marker, line, label); None — вернуть как по умолчанию."""
        style = self.styles.setdefault(key, {})
        for name, value in values.items():
            if value is None:
                style.pop(name, None)
            else:
                style[name] = value
        if not style:
            del self.styles[key]
        self.timer.start()

    def reset_styles(self) -> None:
        """«Сбросить вид кривых»: все кривые — как по умолчанию и показаны."""
        self.styles.clear()
        self.curve_keys = []  # список кривых перестроится
        self.replot()

    def _pick_color(self, key: str) -> None:
        """Щелчок по цвету кривой: выбрать цвет (сначала — её нынешний)."""
        drawn = next((d for d in self.drawn if d.key == key), None)
        current = self._look(drawn)["color"] if drawn is not None else "#000000"
        color = QColorDialog.getColor(QColor(current), self, f"Цвет кривой {key}")
        if color.isValid():
            self.set_style(key, color=color.name())

    def _fill_curve_table(self) -> None:
        """Список «Кривые»: по строке на кривую (одна подпись на всех графиках рядом — одна строка)."""
        keys = list(dict.fromkeys(d.key for d in self.drawn))
        rows = {key: sum(d.rows for d in self.drawn if d.key == key) for key in keys}
        first = {key: next(d for d in self.drawn if d.key == key) for key in keys}
        table = self.curve_table
        table.blockSignals(True)
        rebuild = keys != self.curve_keys
        if rebuild:
            table.setRowCount(len(keys))
            self.curve_keys = keys
        for r, key in enumerate(keys):
            look = self._look(first[key])
            if rebuild:
                shown = QTableWidgetItem()
                shown.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
                table.setItem(r, 0, shown)
                button = QPushButton()
                button.setFixedWidth(28)
                button.clicked.connect(lambda _=False, k=key: self._pick_color(k))
                table.setCellWidget(r, 1, button)
                for column, items, name in ((2, MARKERS, "marker"), (3, LINES, "line")):
                    box = QComboBox()
                    box.addItem(AUTO, None)
                    box.setItemData(0, "как по умолчанию (по «маркер, линия по»)", Qt.ItemDataRole.ToolTipRole)
                    for value, (sign, text) in items.items():
                        box.addItem(sign, value)
                        box.setItemData(box.count() - 1, text, Qt.ItemDataRole.ToolTipRole)
                    box.currentIndexChanged.connect(
                        lambda _, b=box, k=key, n=name: self.set_style(k, **{n: b.currentData()}))
                    table.setCellWidget(r, column, box)
                table.setItem(r, 4, QTableWidgetItem(look["label"]))
                count = QTableWidgetItem(_count(rows[key]))
                count.setFlags(Qt.ItemFlag.ItemIsEnabled)
                table.setItem(r, 5, count)
            table.item(r, 0).setCheckState(Qt.CheckState.Checked if look["visible"] else Qt.CheckState.Unchecked)
            table.cellWidget(r, 1).setStyleSheet(f"background-color: {look['color']}; border: 1px solid #808080;")
            table.cellWidget(r, 1).setToolTip(f"{look['color']} — щелчок: выбрать цвет")
            for column, name in ((2, "marker"), (3, "line")):
                box = table.cellWidget(r, column)
                box.blockSignals(True)
                box.setCurrentIndex(max(box.findData(self.styles.get(key, {}).get(name)), 0))
                box.blockSignals(False)
            table.item(r, 4).setText(look["label"])
            table.item(r, 5).setText(_count(rows[key]))
        table.blockSignals(False)

    def _curve_item_changed(self, item: QTableWidgetItem) -> None:
        """Правка в списке кривых: флажок «показать» или подпись."""
        key = self.curve_keys[item.row()] if item.row() < len(self.curve_keys) else None
        if key is None:
            return
        if item.column() == 0:
            self.set_style(key, visible=None if item.checkState() == Qt.CheckState.Checked else False)
        elif item.column() == 4:
            text = item.text().strip()
            self.set_style(key, label=text if text and text != key else None)

    # ------------------------------------------------------------------ своя кривая и подгонка

    def _fill_parameters(self) -> None:
        """Параметры своей кривой — по формуле (введённые начальные значения остаются)."""
        old = self.initial_values()
        try:
            names = parameters_of(self.curve_formula.text()) if self.curve_formula.text().strip() else []
        except ValueError:
            return
        self.parameters.blockSignals(True)
        self.parameters.setRowCount(len(names))
        for k, name in enumerate(names):
            item = QTableWidgetItem(name)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.parameters.setItem(k, 0, item)
            self.parameters.setItem(k, 1, QTableWidgetItem(f"{old.get(name, 1.0):g}"))
        self.parameters.blockSignals(False)

    def initial_values(self) -> dict[str, float]:
        """Начальные значения параметров из таблицы (не число — 1)."""
        values = {}
        for k in range(self.parameters.rowCount()):
            name, value = self.parameters.item(k, 0), self.parameters.item(k, 1)
            if name is not None:
                number = _number(value.text()) if value is not None else None
                values[name.text()] = 1.0 if number is None else number
        return values

    def set_initial(self, values: dict[str, float]) -> None:
        """Задать начальные значения параметров (как если бы их ввели в таблицу)."""
        for k in range(self.parameters.rowCount()):
            name = self.parameters.item(k, 0).text()
            if name in values:
                self.parameters.setItem(k, 1, QTableWidgetItem(f"{values[name]:g}"))

    def _curves(self, ax, drawn: list[Drawn]) -> list[str]:
        """Своя кривая для кривых drawn одного графика: нарисовать с начальными значениями или подогнать к каждой;
        возвращает строки итога (для подписи под формулой)."""
        formula, mode = self.curve_formula.text().strip(), self.curve_mode.currentData()
        if mode == "off" or not formula:
            return []
        lines, style = [], self._style()
        for d in drawn:
            s, look = d.series, self._look(d)
            finite = np.isfinite(s.x) & np.isfinite(s.y)
            if not finite.any():
                continue
            name = d.name or "кривая"
            values = self.initial_values()
            if mode == "fit":
                result = fit(formula, s.x, s.y, values)
                self.fits.append((name, result))
                if not result.ok:
                    lines.append(f"{name}: {result.message}")
                    continue
                values = result.values
                lines.append(f"{name}: " + ", ".join(f"{p} = {v:.4g} ± {result.errors.get(p, np.nan):.2g}"
                                                     for p, v in values.items()) + f"; R² = {result.r2:.4f}")
            x = np.linspace(np.min(s.x[finite]), np.max(s.x[finite]), CURVE_POINTS)
            try:
                y = np.broadcast_to(evaluate(formula, {"x": x, **values}), x.shape)
            except ValueError as error:
                lines.append(f"формула: {error}")
                break
            suffix = "подгонка" if mode == "fit" else "формула"
            ax.plot(x, y, "--", color=look["color"], linewidth=style["line"],
                    label=f"{look['label']} — {suffix}" if look["label"] else suffix)
            self.curves.append(Series(f"{name} — {suffix}", x, y))
        return lines

    # ------------------------------------------------------------------ график

    def _style(self) -> dict[str, float]:
        """Размеры из раздела «Оформление»: шрифт, толщина линий, размер точек."""
        def size(edit: QLineEdit, default: float) -> float:
            value = _number(edit.text())
            return value if value is not None and value > 0 else default

        return {"font": size(self.font_size, 10), "line": size(self.line_width, 1.2), "marker": size(self.marker_size, 4)}

    def _colors(self, n: int) -> list:
        """Цвета n значений «цвет по» по выбранной палитре."""
        cmap = matplotlib.colormaps[self.palette.currentData()]
        if cmap.N <= 20:  # набор отдельных цветов
            return [cmap(k % cmap.N) for k in range(max(n, 1))]
        return [cmap(v) for v in np.linspace(0.0, 0.9, max(n, 1))]

    def _message(self, text: str) -> None:
        """Пояснение вместо графика."""
        ax = self.figure.add_subplot()
        ax.text(0.5, 0.5, text, ha="center", va="center", transform=ax.transAxes, color="#606060", wrap=True)
        ax.set_axis_off()

    def replot(self) -> None:
        """Пересчитать свои колонки, выборку и кривые по настройкам и нарисовать."""
        kind = self.kind.currentData()
        self.y_column.setEnabled(kind != "histogram")
        self.statistic.setEnabled(kind == "binned")
        self.norm.setEnabled(kind == "histogram")
        for widget in (self.window_width, self.window_count, self.window_low, self.window_high):
            widget.setEnabled(kind != "points")
        self._apply_columns()
        self.figure.clear()
        self.drawn, self.visible, self.series, self.curves, self.fits = [], [], [], [], []
        if not len(self.data):
            self.selection = DataTable()
            self.selection_info.setText("")
            self.summary.setText("Данные не загружены.")
            self._message("Отметьте эксперименты слева и нажмите «Загрузить»")
            return self._finish(False)
        conditions = self._select()
        self.selection_info.setText(f"в выборке {_count(len(self.selection))} строк из {_count(len(self.data))}")
        x_name, y_name = self.x_column.currentData(), self.y_column.currentData()
        if not len(self.selection) or not x_name:
            self.summary.setText(self._summary(kind, x_name, y_name, None, conditions, []))
            self._message("В выборке нет строк — ослабьте условия")
            return self._finish(False)
        width, count = _number(self.window_width.text()), _number(self.window_count.text())
        edges = window_edges(self.selection.columns[x_name], width=width if width and width > 0 else None,
                             count=int(count) if count and count >= 1 else None,
                             low=_number(self.window_low.text()), high=_number(self.window_high.text()))
        self.drawn = self._split(kind, x_name, y_name, edges)
        panels = list(dict.fromkeys(d.panel for d in self.drawn))
        too_many = (f"графиков рядом было бы {len(panels)} — больше {MAX_PANELS}" if len(panels) > MAX_PANELS else
                    f"кривых было бы {len(self.drawn)} — больше {MAX_CURVES}" if len(self.drawn) > MAX_CURVES else "")
        if too_many:
            self.drawn = []
            self.summary.setText(self._summary(kind, x_name, y_name, edges, conditions, []))
            self._message(f"Слишком много: {too_many}.\nВыберите в «5 Разделение» колонку с меньшим числом значений")
            return self._finish(False)
        self.colors = self._colors(1 + max((d.color for d in self.drawn), default=0))
        self.visible = [d for d in self.drawn if self._look(d)["visible"]]
        self.series = [d.series for d in self.visible]
        columns = math.ceil(math.sqrt(len(panels)))
        axes = list(np.ravel(self.figure.subplots(math.ceil(len(panels) / columns), columns, sharex=True, sharey=True,
                                                  squeeze=False)))
        for ax in axes[len(panels):]:
            ax.set_axis_off()
        fit_lines = []
        for panel, ax in zip(panels, axes):
            mine = [d for d in self.visible if d.panel == panel]
            self._draw(ax, kind, mine)
            fit_lines += self._curves(ax, mine)
            self._decorate(ax, kind, x_name, y_name, panel, len(panels) > 1)
        self.fit_info.setText("\n".join(fit_lines))
        self._legend(axes[:len(panels)])
        self.summary.setText(self._summary(kind, x_name, y_name, edges, conditions, panels))
        return self._finish(True)

    def _draw(self, ax, kind: str, drawn: list[Drawn]) -> None:
        """Кривые drawn на оси ax — каждая своим видом."""
        style = self._style()
        for d in drawn:
            s, look = d.series, self._look(d)
            common = {"color": look["color"], "label": look["label"] or None, "linewidth": style["line"]}
            if kind == "histogram":
                ax.stairs(s.y, s.edges, linestyle=look["line"] if look["line"] != "None" else "-", **common)
            elif kind == "binned" and s.low is not None and self.error_bars.isChecked():
                ax.errorbar(s.x, s.y, yerr=[s.y - s.low, s.high - s.y], marker=look["marker"],
                            linestyle=look["line"], markersize=style["marker"], capsize=3, **common)
            elif kind == "binned":
                ax.plot(s.x, s.y, marker=look["marker"], linestyle=look["line"], markersize=style["marker"], **common)
            else:
                marker = look["marker"] if self.styles.get(d.key, {}).get("marker") or self.marker_by.currentData() else "."
                ax.plot(s.x, s.y, marker=marker, linestyle="None", markersize=style["marker"] / 2, alpha=0.5,
                        color=look["color"], label=look["label"] or None)

    def _decorate(self, ax, kind: str, x_name: str, y_name: str, panel: str, several: bool) -> None:
        """Подписи, пределы, лог-оси, сетка, шрифт — по разделам «Оси и подписи» и «Оформление»; у графиков
        рядом — заголовок графика, подписи осей только с краю, общий заголовок сверху."""
        font = self._style()["font"]
        if kind == "histogram":
            y_text = NORMS[self.norm.currentData()]
        elif kind == "binned":
            y_text = f"{self.selection.label(y_name)}: {STATISTICS[self.statistic.currentData()]}"
        else:
            y_text = self.selection.label(y_name)
        ax.set_xlabel(self.x_label.text() or self.selection.label(x_name), fontsize=font)
        ax.set_ylabel(self.y_label.text() or y_text, fontsize=font)
        what = {"binned": f"{y_name} по окнам {x_name}", "histogram": f"распределение {x_name}",
                "points": f"{y_name} от {x_name}"}[kind]
        title = self.title.text() or f"{what}; строк в выборке: {_count(len(self.selection))}"
        if several:
            ax.set_title(panel, fontsize=font)
            self.figure.suptitle(title, fontsize=font)
            ax.label_outer()
        else:
            ax.set_title(title, fontsize=font)
        ax.tick_params(labelsize=font * 0.9)
        if self.log_x.isChecked():
            ax.set_xscale("log")
        if self.log_y.isChecked():
            ax.set_yscale("log")
        ax.set_xlim(_number(self.x_from.text()), _number(self.x_to.text()))
        ax.set_ylim(_number(self.y_from.text()), _number(self.y_to.text()))
        ax.grid(self.grid.isChecked(), alpha=0.3)

    def _legend(self, axes: list) -> None:
        """Легенда: у одного графика — на нём; у графиков рядом — одна, на первом, со всеми кривыми."""
        position = self.legend.currentData()
        if position == "none" or not axes:
            return
        handles, labels = {}, []
        for ax in axes:
            for handle, label in zip(*ax.get_legend_handles_labels()):
                if label not in handles:
                    handles[label] = handle
                    labels.append(label)
        if labels:
            axes[0].legend([handles[label] for label in labels], labels, loc=position,
                           fontsize=self._style()["font"] * 0.85)

    def _summary(self, kind: str, x_name: str | None, y_name: str | None, edges: np.ndarray | None,
                 conditions: list[Condition], panels: list[str]) -> str:
        """Сводка над графиком: что построено, выборка, разделение, сколько кривых."""
        x_text = self.data.label(x_name) if x_name else "—"
        if edges is None:
            what = f"{KINDS[kind]}."
        elif kind == "histogram":
            what = (f"Распределение {x_text}: {len(edges) - 1} окон {edges[0]:.4g}…{edges[-1]:.4g}, "
                    f"{NORMS[self.norm.currentData()]}.")
        elif kind == "binned":
            what = (f"{self.data.label(y_name)} по окнам {x_text}: {len(edges) - 1} окон {edges[0]:.4g}…"
                    f"{edges[-1]:.4g}, {STATISTICS[self.statistic.currentData()]}.")
        else:
            what = f"Точки: X — {x_text}, Y — {self.data.label(y_name)}."
        chosen = "; ".join(_describe(c) for c in conditions) or "все строки"
        selected = f"Выборка: {chosen} → {_count(len(self.selection))} из {_count(len(self.data))} строк."
        parts = []
        for box, text in ((self.color_by, "цвет"), (self.marker_by, "маркер и линия"), (self.facet_by, "графики рядом")):
            name = box.currentData()
            if name and len(self.selection):
                parts.append(f"{text} — {name} ({len(self.selection.unique(name))})")
        grouped = ("Разделение: " + ", ".join(parts) + ".") if parts else "Без разделения."
        hidden = len(self.drawn) - len(self.visible)
        shown = f"Кривых: {len(self.visible)}" + (f" (скрыто {hidden})" if hidden else "") + \
                (f" на {len(panels)} графиках." if len(panels) > 1 else ".")
        return f"{what}  {selected}\n{grouped}  {shown}"

    def _finish(self, drawn: bool) -> None:
        """Перерисовать картинку, список кривых (drawn — кривые есть) и таблицу посчитанного."""
        self.canvas.draw_idle()
        if drawn:
            self._fill_curve_table()
        else:
            self.curve_table.setRowCount(0)
            self.curve_keys = []
        rows = []
        for d in self.visible:
            s = d.series
            if s.edges is None:
                continue
            for k in range(len(s.x)):
                low = "" if s.low is None or np.isnan(s.low[k]) else f"{s.low[k]:.4g}"
                high = "" if s.high is None or np.isnan(s.high[k]) else f"{s.high[k]:.4g}"
                rows.append([d.name, f"{s.edges[k]:.4g} – {s.edges[k + 1]:.4g}",
                             "" if np.isnan(s.y[k]) else f"{s.y[k]:.4g}", low, high,
                             "" if s.count is None else str(int(s.count[k]))])
        self.values.setRowCount(min(len(rows), MAX_TABLE_ROWS))
        for r, row in enumerate(rows[:MAX_TABLE_ROWS]):
            for c, text in enumerate(row):
                self.values.setItem(r, c, QTableWidgetItem(text))

    # ------------------------------------------------------------------ экспорт

    def series_table(self) -> dict[str, np.ndarray]:
        """Показанные кривые (и свои кривые) одной таблицей; колонка series — подпись «график | кривая»."""
        tables = [DataTable({"series": np.full(len(d.series.x), d.name, dtype=object), **d.series.columns()})
                  for d in self.visible]
        tables += [DataTable({"series": np.full(len(s.x), s.label, dtype=object), **s.columns()}) for s in self.curves]
        return DataTable.concat(tables).columns if tables else {}

    def fit_table(self) -> dict[str, np.ndarray]:
        """Параметры подгонки: по строке на параметр каждой кривой (значение, погрешность, R², точек)."""
        rows = [(name, p, v, result.errors.get(p, np.nan), result.r2, result.points)
                for name, result in self.fits if result.ok for p, v in result.values.items()]
        columns = ("series", "parameter", "value", "error", "r2", "points")
        return {c: np.array([row[k] for row in rows], dtype=object if k < 2 else np.float64)
                for k, c in enumerate(columns)}

    def export(self, path: Path, what: str) -> None:
        """Записать what: "selection" — выборка в CSV; "series" — посчитанное в CSV; "labplot" — проект LabPlot
        с таблицей выборки, таблицей на каждую показанную кривую и таблицей параметров подгонки."""
        if what == "selection":
            write_csv(path, self.selection.columns)
        elif what == "series":
            write_csv(path, self.series_table())
        else:
            sheets = {"выборка": self.selection.columns}
            named = [(d.name, d.series) for d in self.visible] + [(s.label, s) for s in self.curves]
            for k, (name, s) in enumerate(named, start=1):
                sheets[f"{k} {name}".strip() if name else f"{k} кривая"] = s.columns()
            if self.fits:
                sheets["параметры подгонки"] = self.fit_table()
            write_lml(path, sheets)

    def _export_dialog(self, what: str) -> None:
        """Кнопка экспорта: спросить, куда записать, и записать."""
        if not len(self.selection):
            self.message.emit("Нечего записывать: выборка пуста")
            return
        labplot = what == "labplot"
        path, _ = QFileDialog.getSaveFileName(self, "Куда записать", self.last_dir,
                                              "Проект LabPlot (*.lml)" if labplot else "Таблица CSV (*.csv)")
        if not path:
            return
        try:
            self.export(Path(path), what)
        except OSError as error:
            QMessageBox.warning(self, "Не записано", str(error))
            return
        self.last_dir = str(Path(path).parent)
        self.message.emit(f"Записано: {path}")

    # ------------------------------------------------------------------ настройки в файл

    def state(self) -> dict[str, Any]:
        """Все настройки части (для сохранения в файл .json)."""
        edits = {name: getattr(self, name).text() for name in
                 ("window_width", "window_count", "window_low", "window_high", "curve_formula", "title", "x_label",
                  "y_label", "x_from", "x_to", "y_from", "y_to", "font_size", "line_width", "marker_size")}
        combos = {name: getattr(self, name).currentData() for name in
                  ("level", "kind", "x_column", "y_column", "color_by", "marker_by", "facet_by", "statistic", "norm",
                   "curve_mode", "legend", "palette")}
        checks = {name: getattr(self, name).isChecked() for name in ("log_x", "log_y", "grid", "error_bars")}
        return {"sources": [s.label for s in self.checked_sources()], "columns": self.columns_text.toPlainText(),
                "conditions": [row.state() for row in self.rows], "initial": self.initial_values(),
                "edits": edits, "combos": combos, "checks": checks, "styles": self.styles}

    def set_state(self, state: dict[str, Any]) -> None:
        """Восстановить настройки: источники (и загрузить данные), свои колонки, условия, график, вид кривых.
        Старые файлы («серии по» — series_by) читаются: это «цвет по»."""
        combos = dict(state.get("combos", {}))
        if "series_by" in combos:
            combos.setdefault("color_by", combos.pop("series_by"))
        self.level.setCurrentIndex(max(self.level.findData(combos.get("level")), 0))
        self.set_checked(state.get("sources", []))
        self.columns_text.setPlainText(state.get("columns", ""))
        if self.checked_sources():
            self.load_data()
        for row in list(self.rows):
            self._remove_condition(row)
        for condition in state.get("conditions", []):
            self.add_condition().set_state(condition)
        for name, text in state.get("edits", {}).items():
            if isinstance(getattr(self, name, None), QLineEdit):
                getattr(self, name).setText(text)
        self.set_initial(state.get("initial", {}))
        for name, key in combos.items():
            box = getattr(self, name, None)
            if isinstance(box, QComboBox):
                box.setCurrentIndex(max(box.findData(key), 0))
        for name, checked in state.get("checks", {}).items():
            if isinstance(getattr(self, name, None), QCheckBox):
                getattr(self, name).setChecked(checked)
        self.styles = {key: dict(style) for key, style in state.get("styles", {}).items()}
        self.curve_keys = []
        self.replot()

    def _save_state_dialog(self) -> None:
        """«Сохранить настройки…»: в файл .json."""
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить настройки", self.last_dir, "Настройки (*.json)")
        if path:
            Path(path).write_text(json.dumps(self.state(), ensure_ascii=False, indent=1), encoding="utf-8")
            self.last_dir = str(Path(path).parent)
            self.message.emit(f"Настройки сохранены: {path}")

    def _load_state_dialog(self) -> None:
        """«Загрузить настройки…»: из файла .json."""
        path, _ = QFileDialog.getOpenFileName(self, "Загрузить настройки", self.last_dir, "Настройки (*.json)")
        if not path:
            return
        try:
            self.set_state(json.loads(Path(path).read_text(encoding="utf-8")))
        except (OSError, ValueError, AttributeError) as error:
            QMessageBox.warning(self, "Настройки не загружены", f"{type(error).__name__}: {error}")
            return
        self.last_dir = str(Path(path).parent)
        self.message.emit(f"Настройки загружены: {path}")
