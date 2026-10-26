"""Часть «Обработка результатов»: распределения и зависимости по итоговым файлам st4 многих экспериментов.

Не привязана к текущему эксперименту. Слева — разделы (щелчок по заголовку сворачивает и разворачивает):
    Данные           эксперименты из папки experiments/ (результаты Python и, если задан MATLAB_RESULTS_DIR,
                     MATLAB) — отметить галочками; уровень: «траектории в областях» (строка на траекторию
                     в области st4: x, y, мм; vx, vy, |v|, м/с; r, мкм; высота и нижний край области, мм) или
                     «средние по областям» (_all_stat.dat); к строкам добавляются параметры записи из таблицы
                     записей (F, cond_num, bag_hight, …). «Загрузить» — прочитать.
    Свои колонки     строки «имя = формула», например «U = 0.07*F» — новые колонки для осей и выборки.
    Выборка          условия: колонка «равна» выбранным значениям (например, F = 140, одна высота области) или
                     лежит «от–до»; действуют все условия сразу — это и есть «фиксирование параметров».
    График           распределение (гистограмма) X; зависимость Y от X по окнам X (среднее ± СКО, ± ошибка
                     среднего, медиана с квартилями, число); точки. Окна — ширина или число, пределы «от–до».
                     «Серии по» — отдельная кривая на каждое значение колонки (например, F или запись).
    Кривая и подгонка  своя функция f(x) с параметрами, например «a*x**b + c»: нарисовать с заданными значениями
                     или подогнать параметры к каждой кривой (наименьшие квадраты; параметры ± погрешность, R²).
    Вид              заголовок и подписи осей, пределы, логарифмические оси, сетка, легенда, шрифт, толщина
                     линий, размер точек, цвета, усы разброса.
    Экспорт и настройки  выборка и посчитанное — в CSV или в проект LabPlot (.lml); все настройки этой части —
                     сохранить в файл и загрузить (повторить картинку позже).
В середине — картинка (увеличение, сдвиг и сохранение PNG/SVG/PDF — панель под ней), ниже — таблица посчитанного.
Любая правка слева через короткую паузу перестраивает картинку. Расчёты — lib/analysis/.
"""

import json
import logging
from pathlib import Path
from typing import Any

import matplotlib
import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSplitter, QTableWidget,
                               QTableWidgetItem, QToolButton, QVBoxLayout, QWidget)

from lib.analysis.dataset import LEVELS, DataTable, Source, find_sources, load
from lib.analysis.formulas import FitResult, computed_columns, evaluate, fit, parameters_of
from lib.analysis.labplot import write_csv, write_lml
from lib.analysis.stats import (NORMS, STATISTICS, Condition, Series, binned, histogram, label_value, points,
                                select, split, window_edges)

log = logging.getLogger(__name__)

KINDS = {"binned": "зависимость Y от X по окнам X", "histogram": "распределение (гистограмма) X",
         "points": "точки (X, Y)"}
CURVE_MODES = {"off": "не показывать", "draw": "нарисовать с этими значениями", "fit": "подогнать к каждой кривой"}
LEGENDS = {"best": "сама", "upper right": "справа вверху", "upper left": "слева вверху",
           "lower right": "справа внизу", "lower left": "слева внизу", "none": "не показывать"}
PALETTES = {"tab10": "обычные", "Set1": "яркие", "Dark2": "тёмные", "viridis": "синий → жёлтый",
            "plasma": "синий → оранжевый", "cividis": "для печати"}
MAX_VALUES = 300  # «равно»: список значений колонки показывается, если разных значений не больше
MAX_SERIES_VALUES = 40  # «серии по»: колонки, у которых разных значений не больше
MAX_TABLE_ROWS = 3000  # строк в таблице посчитанного
REPLOT_MS = 200  # пауза перед перестройкой картинки после правки
CURVE_POINTS = 300  # точек на своей кривой


def _number(text: str) -> float | None:
    """Число из поля ввода (запятая тоже годится); пусто или не число — None."""
    try:
        return float(text.strip().replace(",", "."))
    except ValueError:
        return None


class Section(QWidget):
    """Раздел левой панели: заголовок-кнопка со стрелкой (щелчок — свернуть / развернуть) и содержимое.
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
    «×» — убрать условие. table — загруженные данные (для списка колонок и значений)."""

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
        self.loaded = DataTable()  # прочитанное из файлов
        self.data = DataTable()  # + свои колонки
        self.selection = DataTable()
        self.series: list[Series] = []
        self.curves: list[Series] = []  # свои кривые (нарисованные или подогнанные)
        self.fits: list[tuple[str, FitResult]] = []
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

    def _build(self) -> None:
        """Слева — разделы настроек, справа — картинка и таблица посчитанного."""
        sections = [self._data_section(), self._columns_section(), self._selection_section(),
                    self._plot_section(), self._curve_section(), self._view_section(), self._export_section()]
        panel = QWidget()
        side = QVBoxLayout(panel)
        for section in sections:
            side.addWidget(section)
        side.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(panel)
        scroll.setMinimumWidth(390)

        self.figure = Figure(figsize=(8, 6))
        self.canvas = FigureCanvasQTAgg(self.figure)
        plot = QWidget()
        column = QVBoxLayout(plot)
        column.setContentsMargins(0, 0, 0, 0)
        column.addWidget(self.canvas, 1)
        column.addWidget(NavigationToolbar2QT(self.canvas, plot))
        self.values = QTableWidget(0, 6)
        self.values.setHorizontalHeaderLabels(["кривая", "x", "y", "разброс от", "до", "число"])
        self.values.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.values.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        right = QSplitter(Qt.Orientation.Vertical)
        right.addWidget(plot)
        right.addWidget(self.values)
        right.setSizes([650, 220])
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(scroll)
        splitter.addWidget(right)
        splitter.setStretchFactor(1, 1)
        splitter.setChildrenCollapsible(False)
        layout = QVBoxLayout(self)
        layout.addWidget(splitter)

    def _data_section(self) -> Section:
        """Раздел «Данные»: источники, уровень, «Загрузить»."""
        section = Section("Данные")
        self.source_list = QListWidget()
        self.source_list.setMaximumHeight(120)
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
        """Раздел «Свои колонки»: строки «имя = формула»."""
        section = Section("Свои колонки", expanded=False)
        self.columns_text = QPlainTextEdit()
        self.columns_text.setPlaceholderText("по строке «имя = формула», например:\nU = 0.07*F\nd = 2*r")
        self.columns_text.setMaximumHeight(80)
        self.columns_text.setToolTip("Формулы — по числовым колонкам данных; функции: sqrt, exp, log, sin, cos, abs, …")
        self.columns_text.textChanged.connect(self.timer.start)
        self.columns_info = QLabel()
        self.columns_info.setWordWrap(True)
        section.body.addWidget(self.columns_text)
        section.body.addWidget(self.columns_info)
        return section

    def _selection_section(self) -> Section:
        """Раздел «Выборка»: условия."""
        section = Section("Выборка")
        self.conditions = QVBoxLayout()
        add = QPushButton("+ условие")
        add.setToolTip("колонка равна выбранным значениям или лежит от–до; действуют все условия сразу")
        add.clicked.connect(lambda: self.add_condition())
        self.selection_info = QLabel()
        section.body.addLayout(self.conditions)
        section.body.addWidget(add)
        section.body.addWidget(self.selection_info)
        return section

    def _plot_section(self) -> Section:
        """Раздел «График»: вид, оси, окна, статистика, серии."""
        section = Section("График")
        self.kind = self._combo(KINDS)
        self.x_column, self.y_column, self.series_by = QComboBox(), QComboBox(), QComboBox()
        for box in (self.x_column, self.y_column, self.series_by):
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
        form.addRow("пределы X", bounds)
        form.addRow("по окнам", self.statistic)
        form.addRow("нормировка", self.norm)
        form.addRow("серии по", self.series_by)
        section.body.addLayout(form)
        return section

    def _curve_section(self) -> Section:
        """Раздел «Кривая и подгонка»: формула f(x), параметры, режим, результат."""
        section = Section("Кривая и подгонка", expanded=False)
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

    def _view_section(self) -> Section:
        """Раздел «Вид»: подписи, пределы, оси, легенда, шрифт, линии, цвета."""
        section = Section("Вид", expanded=False)
        self.title = self._edit("заголовок: авто")
        self.x_label, self.y_label = self._edit("подпись X: авто"), self._edit("подпись Y: авто")
        self.x_from, self.x_to = self._edit("X от"), self._edit("X до")
        self.y_from, self.y_to = self._edit("Y от"), self._edit("Y до")
        self.log_x, self.log_y = self._check("лог. X"), self._check("лог. Y")
        self.grid, self.error_bars = self._check("сетка", True), self._check("разброс (усы)", True)
        self.legend = self._combo(LEGENDS)
        self.palette = self._combo(PALETTES)
        self.font_size, self.line_width, self.marker_size = self._edit("10"), self._edit("1.2"), self._edit("4")
        form = QFormLayout()
        form.addRow("заголовок", self.title)
        form.addRow("подпись X", self.x_label)
        form.addRow("подпись Y", self.y_label)
        for label, a, b in (("пределы X", self.x_from, self.x_to), ("пределы Y", self.y_from, self.y_to),
                            ("оси", self.log_x, self.log_y), ("", self.grid, self.error_bars)):
            row = QHBoxLayout()
            row.addWidget(a)
            row.addWidget(b)
            form.addRow(label, row)
        form.addRow("легенда", self.legend)
        form.addRow("цвета", self.palette)
        sizes = QHBoxLayout()
        for text, widget in (("шрифт", self.font_size), ("линии", self.line_width), ("точки", self.marker_size)):
            sizes.addWidget(QLabel(text))
            sizes.addWidget(widget)
        form.addRow(sizes)
        section.body.addLayout(form)
        return section

    def _export_section(self) -> Section:
        """Раздел «Экспорт и настройки»."""
        section = Section("Экспорт и настройки")
        for text, slot, tip in (
                ("Выборка → CSV", lambda: self._export_dialog("selection"), "все строки выборки, все колонки"),
                ("Посчитанное → CSV", lambda: self._export_dialog("series"),
                 "кривые графика: окна, значения, разброс, число; свои кривые"),
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
        """Список источников: эксперименты папки experiments/ (отмеченные галочки остаются)."""
        checked = {s.label for s in self.checked_sources()}
        self.sources, problems = find_sources(self.experiments_dir) if self.experiments_dir.is_dir() else ([], [])
        self.source_list.clear()
        for source in self.sources:
            item = QListWidgetItem(source.label)
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
            item.setCheckState(Qt.CheckState.Checked if item.text() in labels else Qt.CheckState.Unchecked)

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
        records = len(set(zip(self.loaded.columns.get("experiment", []), self.loaded.columns.get("record", []))))
        self.data_info.setText(f"Загружено строк: {len(self.loaded)}, записей: {records}"
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
        """Списки колонок X, Y и «серии по» — по данным (выбранное остаётся, если есть)."""
        numeric = [n for n in self.data.names if self.data.is_numeric(n)]
        few = [n for n in self.data.names if len(self.data.unique(n)) <= MAX_SERIES_VALUES]
        for box, names, default in ((self.x_column, numeric, "r"), (self.y_column, numeric, "v"),
                                    (self.series_by, ["", *few], "")):
            old = box.currentData()
            box.blockSignals(True)
            box.clear()
            for name in names:
                box.addItem(self.data.label(name) if name else "— (одна кривая)", name)
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

    # ------------------------------------------------------------------ кривая и подгонка

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

    def _curves(self, ax, colors: list) -> None:
        """Своя кривая: нарисовать с начальными значениями или подогнать к каждой кривой; итог — под формулой."""
        self.curves, self.fits = [], []
        formula, mode = self.curve_formula.text().strip(), self.curve_mode.currentData()
        if mode == "off" or not formula or not self.series:
            self.fit_info.setText("")
            return
        lines, style = [], self._style()
        for s, color in zip(self.series, colors):
            finite = np.isfinite(s.x) & np.isfinite(s.y)
            if not finite.any():
                continue
            name = s.label or "кривая"
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
            ax.plot(x, y, "--", color=color, linewidth=style["line"], label=f"{s.label} — {suffix}" if s.label else suffix)
            self.curves.append(Series(f"{name} — {suffix}", x, y))
        self.fit_info.setText("\n".join(lines))

    # ------------------------------------------------------------------ график

    def _style(self) -> dict[str, float]:
        """Размеры из раздела «Вид»: шрифт, толщина линий, размер точек."""
        def size(edit: QLineEdit, default: float) -> float:
            value = _number(edit.text())
            return value if value is not None and value > 0 else default

        return {"font": size(self.font_size, 10), "line": size(self.line_width, 1.2), "marker": size(self.marker_size, 4)}

    def _colors(self, n: int) -> list:
        """Цвета n кривых по выбранной палитре."""
        cmap = matplotlib.colormaps[self.palette.currentData()]
        if cmap.N <= 20:  # набор отдельных цветов
            return [cmap(k % cmap.N) for k in range(n)]
        return [cmap(v) for v in np.linspace(0.0, 0.9, max(n, 1))]

    def _message(self, ax, text: str) -> None:
        """Пояснение вместо графика."""
        ax.text(0.5, 0.5, text, ha="center", va="center", transform=ax.transAxes, color="#606060", wrap=True)
        ax.set_axis_off()

    def replot(self) -> None:
        """Пересчитать свои колонки, выборку и кривые по настройкам слева и нарисовать."""
        kind = self.kind.currentData()
        self.y_column.setEnabled(kind != "histogram")
        self.statistic.setEnabled(kind == "binned")
        self.norm.setEnabled(kind == "histogram")
        for widget in (self.window_width, self.window_count, self.window_low, self.window_high):
            widget.setEnabled(kind != "points")
        self._apply_columns()
        self.figure.clear()
        ax = self.figure.add_subplot()
        self.series, self.curves, self.fits = [], [], []
        if not len(self.data):
            self.selection = DataTable()
            self.selection_info.setText("")
            self._message(ax, "Отметьте эксперименты слева и нажмите «Загрузить»")
            return self._finish()
        self.selection = select(self.data, [c for c in (row.condition() for row in self.rows) if c is not None])
        self.selection_info.setText(f"в выборке {len(self.selection)} строк из {len(self.data)}")
        x_name, y_name, by = self.x_column.currentData(), self.y_column.currentData(), self.series_by.currentData()
        if not len(self.selection) or not x_name:
            self._message(ax, "В выборке нет строк — ослабьте условия")
            return self._finish()
        width, count = _number(self.window_width.text()), _number(self.window_count.text())
        edges = window_edges(self.selection.columns[x_name], width=width if width and width > 0 else None,
                             count=int(count) if count and count >= 1 else None,
                             low=_number(self.window_low.text()), high=_number(self.window_high.text()))
        for label, part in split(self.selection, by or None):
            if kind == "histogram":
                self.series.append(histogram(part.columns[x_name], edges, self.norm.currentData(), label))
            elif kind == "binned":
                self.series.append(binned(part.columns[x_name], part.columns[y_name], edges,
                                          self.statistic.currentData(), label))
            else:
                self.series.append(points(part.columns[x_name], part.columns[y_name], label))
        colors = self._colors(len(self.series))
        self._draw(ax, kind, x_name, y_name, colors)
        self._curves(ax, colors)
        self._decorate(ax, kind, x_name, y_name)
        return self._finish()

    def _draw(self, ax, kind: str, x_name: str, y_name: str, colors: list) -> None:
        """Кривые self.series на оси ax."""
        style = self._style()
        for s, color in zip(self.series, colors):
            label = s.label or None
            if kind == "histogram":
                ax.stairs(s.y, s.edges, label=label, linewidth=style["line"], color=color)
            elif kind == "binned" and s.low is not None and self.error_bars.isChecked():
                ax.errorbar(s.x, s.y, yerr=[s.y - s.low, s.high - s.y], fmt="o-", markersize=style["marker"],
                            capsize=3, linewidth=style["line"], color=color, label=label)
            elif kind == "binned":
                ax.plot(s.x, s.y, "o-", markersize=style["marker"], linewidth=style["line"], color=color, label=label)
            else:
                ax.plot(s.x, s.y, ".", markersize=style["marker"] / 2, alpha=0.5, color=color, label=label)

    def _decorate(self, ax, kind: str, x_name: str, y_name: str) -> None:
        """Подписи, пределы, лог-оси, сетка, легенда, шрифт — по разделу «Вид»."""
        font = self._style()["font"]
        if kind == "histogram":
            y_text = NORMS[self.norm.currentData()]
        elif kind == "binned":
            y_text = f"{self.selection.label(y_name)}: {STATISTICS[self.statistic.currentData()]}"
        else:
            y_text = self.selection.label(y_name)
        ax.set_xlabel(self.x_label.text() or self.selection.label(x_name), fontsize=font)
        ax.set_ylabel(self.y_label.text() or y_text, fontsize=font)
        ax.set_title(self.title.text() or f"{KINDS[kind]}; строк в выборке: {len(self.selection)}", fontsize=font)
        ax.tick_params(labelsize=font * 0.9)
        if self.log_x.isChecked():
            ax.set_xscale("log")
        if self.log_y.isChecked():
            ax.set_yscale("log")
        ax.set_xlim(_number(self.x_from.text()), _number(self.x_to.text()))
        ax.set_ylim(_number(self.y_from.text()), _number(self.y_to.text()))
        ax.grid(self.grid.isChecked(), alpha=0.3)
        position = self.legend.currentData()
        if position != "none" and ax.get_legend_handles_labels()[1]:
            ax.legend(loc=position, fontsize=font * 0.85)

    def _finish(self) -> None:
        """Перерисовать картинку и таблицу посчитанного."""
        self.canvas.draw_idle()
        rows = []
        for s in self.series:
            if s.edges is None:
                continue
            for k in range(len(s.x)):
                low = "" if s.low is None or np.isnan(s.low[k]) else f"{s.low[k]:.4g}"
                high = "" if s.high is None or np.isnan(s.high[k]) else f"{s.high[k]:.4g}"
                rows.append([s.label, f"{s.edges[k]:.4g} – {s.edges[k + 1]:.4g}",
                             "" if np.isnan(s.y[k]) else f"{s.y[k]:.4g}", low, high,
                             "" if s.count is None else str(int(s.count[k]))])
        self.values.setRowCount(min(len(rows), MAX_TABLE_ROWS))
        for r, row in enumerate(rows[:MAX_TABLE_ROWS]):
            for c, text in enumerate(row):
                self.values.setItem(r, c, QTableWidgetItem(text))

    # ------------------------------------------------------------------ экспорт

    def series_table(self) -> dict[str, np.ndarray]:
        """Все кривые (и свои кривые) одной таблицей; колонка series — подпись кривой."""
        tables = [DataTable({"series": np.full(len(s.x), s.label, dtype=object), **s.columns()})
                  for s in [*self.series, *self.curves]]
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
        с таблицей выборки, таблицей на каждую кривую и таблицей параметров подгонки."""
        if what == "selection":
            write_csv(path, self.selection.columns)
        elif what == "series":
            write_csv(path, self.series_table())
        else:
            sheets = {"выборка": self.selection.columns}
            for k, s in enumerate([*self.series, *self.curves], start=1):
                sheets[f"{k} {s.label}".strip() if s.label else f"{k} кривая"] = s.columns()
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
                 ("window_width", "window_count", "window_low", "window_high", "curve_formula", "title", "x_label", "y_label", "x_from", "x_to",
                  "y_from", "y_to", "font_size", "line_width", "marker_size")}
        combos = {name: getattr(self, name).currentData() for name in
                  ("level", "kind", "x_column", "y_column", "series_by", "statistic", "norm", "curve_mode", "legend",
                   "palette")}
        checks = {name: getattr(self, name).isChecked() for name in ("log_x", "log_y", "grid", "error_bars")}
        return {"sources": [s.label for s in self.checked_sources()], "columns": self.columns_text.toPlainText(),
                "conditions": [row.state() for row in self.rows], "initial": self.initial_values(),
                "edits": edits, "combos": combos, "checks": checks}

    def set_state(self, state: dict[str, Any]) -> None:
        """Восстановить настройки: источники (и загрузить данные), свои колонки, условия, график, вид."""
        combos = state.get("combos", {})
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
            getattr(self, name).setText(text)
        self.set_initial(state.get("initial", {}))
        for name, key in combos.items():
            box = getattr(self, name)
            box.setCurrentIndex(max(box.findData(key), 0))
        for name, checked in state.get("checks", {}).items():
            getattr(self, name).setChecked(checked)
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
