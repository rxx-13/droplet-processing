"""Вкладка «Результаты»: траектории (st2) и области поиска (st4) записи, таблицы, папки результатов.

Запись — в списке вверху (при открытии вкладки — текущая запись «Записей»; в списке видно, какие результаты
у записи есть). «Траектории» — картинка как tracks.png (draw_tracks из lib/st2/tracking.py) по
ptv_unfiltered.mat. «Области st4» — таблица _all_stat.dat: по строке на область с траекториями (высота
и нижний край, мм; число траекторий; средние vx, vy, м/с; r, мкм). Выбор строки — картинка области
(draw_area из lib/st4/areas.py — по траекториям и границам области из её .mat) и таблица её .dat
(по строке на траекторию: x, y, мм; vx, vy, м/с; r, мкм). Увеличение и сдвиг — панель инструментов под
картинкой (лупа, крестик; домик — вернуть). «Папка st1 / st2 / st4» — открыть папку результатов в проводнике.
Картинки рисуются с текущими масштабом (SCALE_MM_PER_PX), центром (c_x, c_y) и настройками графиков.
"""

from pathlib import Path

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QLabel, QPushButton,
                               QSplitter, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)
from scipy.io import loadmat

from app.records_page import RESULT_FILES, RecordsPage
from lib.common import files
from lib.st2.tracking import draw_tracks
from lib.st4.areas import Area, area_file_name, draw_area, tracks_in_area

SUMMARY_COLUMNS = ["высота, мм", "нижний край, мм", "траекторий", "vx, м/с", "vy, м/с", "r, мкм"]  # _all_stat.dat
AREA_COLUMNS = ["x, мм", "y, мм", "vx, м/с", "vy, м/с", "r, мкм"]  # <область>.dat


def _plot() -> tuple[QWidget, Figure, FigureCanvasQTAgg]:
    """Картинка matplotlib с панелью инструментов (увеличение, сдвиг, сохранить): (виджет, рисунок, холст)."""
    figure = Figure(figsize=(8, 6))
    canvas = FigureCanvasQTAgg(figure)
    widget = QWidget()
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(canvas, 1)
    layout.addWidget(NavigationToolbar2QT(canvas, widget))
    return widget, figure, canvas


def _fill_table(table: QTableWidget, data: np.ndarray, integer_columns: tuple[int, ...] = ()) -> None:
    """Таблица чисел (строки × столбцы) в QTableWidget: 4 значащие цифры, целые столбцы — без дробной части."""
    data = np.atleast_2d(data) if np.size(data) else np.zeros((0, table.columnCount()))
    table.setRowCount(data.shape[0])
    for r, row in enumerate(data):
        for c in range(table.columnCount()):
            value = row[c] if c < len(row) else np.nan
            text = f"{value:.0f}" if c in integer_columns else f"{value:.4g}"
            item = QTableWidgetItem(text)
            item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            table.setItem(r, c, item)


def _table(columns: list[str]) -> QTableWidget:
    """Пустая таблица только для чтения с заголовками columns."""
    table = QTableWidget(0, len(columns))
    table.setHorizontalHeaderLabels(columns)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    return table


class ResultsPage(QWidget):
    """Вкладка «Результаты». records — вкладка «Записи» (таблица, настройки, отметки результатов)."""

    def __init__(self, records: RecordsPage) -> None:
        super().__init__()
        self.records = records
        self.i = -1  # показанная запись (строка таблицы)
        self.tracks: list[np.ndarray] = []
        self.record_box = QComboBox()
        self.record_box.setMinimumWidth(420)
        self.record_box.currentIndexChanged.connect(lambda _: self.show_record(self.record_box.currentData()))
        top = QHBoxLayout()
        top.addWidget(QLabel("Запись:"))
        top.addWidget(self.record_box)
        self.folder_buttons = {}
        for stage in RESULT_FILES:
            b = QPushButton(f"Папка {stage}")
            b.setToolTip(f"открыть папку результатов {stage} этой записи")
            b.clicked.connect(lambda checked=False, s=stage: self.open_folder(s))
            top.addWidget(b)
            self.folder_buttons[stage] = b
        refresh = QPushButton("Обновить")
        refresh.setToolTip("перечитать результаты (например, после обработки скриптами main/)")
        refresh.clicked.connect(lambda: self.refresh(self.i))
        top.addWidget(refresh)
        top.addStretch(1)

        tracks_widget, self.tracks_figure, self.tracks_canvas = _plot()
        self.tracks_info = QLabel()
        tracks_page = QWidget()
        column = QVBoxLayout(tracks_page)
        column.addWidget(self.tracks_info)
        column.addWidget(tracks_widget, 1)

        self.summary = _table(SUMMARY_COLUMNS)
        self.summary.itemSelectionChanged.connect(self._area_selected)
        self.area_table = _table(AREA_COLUMNS)
        self.areas_info = QLabel()
        self.areas_info.setWordWrap(True)
        self.area_info = QLabel()
        self.area_info.setWordWrap(True)
        left = QWidget()
        column = QVBoxLayout(left)
        column.setContentsMargins(0, 0, 0, 0)
        column.addWidget(self.areas_info)
        column.addWidget(self.summary, 1)
        column.addWidget(self.area_info)
        column.addWidget(self.area_table, 2)
        area_widget, self.area_figure, self.area_canvas = _plot()
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(area_widget)
        splitter.setSizes([520, 900])

        self.tabs = QTabWidget()
        self.tabs.addTab(tracks_page, "Траектории (st2)")
        self.tabs.addTab(splitter, "Области (st4)")
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.tabs, 1)

    def refresh(self, i: int | None = None) -> None:
        """Перечитать список записей и результаты; показать запись i (по умолчанию — текущую на «Записях»)."""
        table = self.records.table
        i = self.records.i if i is None or i < 0 else i
        self.record_box.blockSignals(True)
        self.record_box.clear()
        for row in range(len(table) if table is not None else 0):
            present = [stage for stage, ok in self.records.results_present.get(row, {}).items() if ok]
            self.record_box.addItem(f"{table.name(row)}   [{', '.join(present) or 'результатов нет'}]", row)
        self.record_box.setCurrentIndex(max(self.record_box.findData(i), 0))
        self.record_box.blockSignals(False)
        self.show_record(self.record_box.currentData())

    def _folder(self, stage: str) -> Path | None:
        """Папка результатов стадии stage для показанной записи (None — записи нет)."""
        record = self.records.table.record(self.i) if self.records.table is not None and self.i >= 0 else None
        if record is None:
            return None
        return getattr(record, RESULT_FILES[stage][0])(Path(self.records.settings.OUTPUT_DIR))

    def open_folder(self, stage: str) -> None:
        """Открыть папку результатов стадии stage в проводнике."""
        folder = self._folder(stage)
        if folder is None or not folder.is_dir():
            self.records.message.emit(f"Папки результатов {stage} нет: {folder}")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def show_record(self, i: int | None) -> None:
        """Показать результаты записи i: траектории и таблицу областей."""
        self.i = -1 if i is None else i
        for stage, button in self.folder_buttons.items():
            folder = self._folder(stage)
            button.setEnabled(folder is not None and folder.is_dir())
        self._draw_tracks()
        self._fill_summary()

    def _draw_tracks(self) -> None:
        """Траектории записи (как tracks.png)."""
        self.tracks_figure.clear()
        self.tracks = []
        folder = self._folder("st2")
        path = folder / RESULT_FILES["st2"][1] if folder is not None else None
        if path is None or not path.exists():
            self.tracks_info.setText("Траекторий нет: запустите st2 для этой записи (вкладка «Запуск»)")
        else:
            self.tracks = files.load_cell(path, "p_arr")
            long_tracks = sum(t.shape[0] > self.records.thresholds.TRACKS_PLOT_MIN_POINTS for t in self.tracks)
            draw_tracks(self.tracks_figure.add_subplot(), self.tracks, self.records.thresholds.TRACKS_PLOT_MIN_POINTS)
            self.tracks_info.setText(f"траекторий {len(self.tracks)}, из них длиннее "
                                     f"{self.records.thresholds.TRACKS_PLOT_MIN_POINTS} точек — {long_tracks}; {path}")
        self.tracks_canvas.draw_idle()

    def _fill_summary(self) -> None:
        """Таблица _all_stat.dat записи (области с траекториями)."""
        self.summary.blockSignals(True)
        folder = self._folder("st4")
        path = folder / RESULT_FILES["st4"][1] if folder is not None else None
        stat = files.load_dat(path) if path is not None and path.exists() else np.zeros((0, 6))
        _fill_table(self.summary, stat, integer_columns=(2,))
        self.summary.blockSignals(False)
        if path is None or not path.exists():
            self.areas_info.setText("Результатов st4 нет: запустите st4 для этой записи (вкладка «Запуск»)")
        else:
            self.areas_info.setText(f"Области с траекториями ({path.name}): выберите строку — картинка и таблица области")
        self.area_table.setRowCount(0)
        self.area_info.setText("")
        self.area_figure.clear()
        self.area_canvas.draw_idle()
        if self.summary.rowCount():
            self.summary.selectRow(0)

    def _area_selected(self) -> None:
        """Выбрана область в таблице: её картинка и таблица её .dat."""
        rows = self.summary.selectionModel().selectedRows()
        folder = self._folder("st4")
        record = self.records.table.record(self.i)
        if not rows or folder is None or record is None:
            return
        row = rows[0].row()
        stat = np.atleast_2d(files.load_dat(folder / RESULT_FILES["st4"][1]))
        height_mm, low_edge_mm = float(stat[row, 0]), float(stat[row, 1])
        name = area_file_name(height_mm, low_edge_mm)
        mat_path, dat_path = folder / f"{name}.mat", folder / f"{name}.dat"
        self.area_figure.clear()
        if dat_path.exists():
            _fill_table(self.area_table, files.load_dat(dat_path))
        if not mat_path.exists() or not self.tracks:
            self.area_info.setText(f"Нет файла области или траекторий: {mat_path.name}")
        elif record.cx is None or record.cy is None:
            self.area_info.setText("Не задан центр c_x, c_y — картинку области не построить")
        else:
            mat = loadmat(mat_path)
            area = Area(x=tuple(mat["search_area_x"].ravel()), y=tuple(mat["search_area_y"].ravel()))
            means, inside = tracks_in_area(self.tracks, area)
            top, bottom = self.area_figure.subplots(2, 1)
            t, s = self.records.thresholds, self.records.settings
            draw_area(top, bottom, self.tracks, inside, means, area, s.SCALE_MM_PER_PX, record.cx, record.cy,
                      t.AREA_PLOT_ARROW_SCALE, t.AREA_PLOT_Y_LIMITS_MM)
            self.area_info.setText(f"Область: высота {height_mm:g} мм, нижний край {low_edge_mm:g} мм; "
                                   f"траекторий {means.shape[0]} (по строке на траекторию ниже)")
        self.area_canvas.draw_idle()
