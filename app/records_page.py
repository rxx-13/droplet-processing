"""Вкладка «Записи»: список записей таблицы, кадры записи, поля таблицы, автозаполнение, сохранение.

Слева — все записи таблицы (CONDITIONS и RECORDS из settings здесь не действуют): готова ли запись
(диапазон кадров, rim, центр c_x/c_y) и есть ли результаты st1, st2, st4 в OUTPUT_DIR; несохранённые
правки — «*». Можно выделить несколько записей (Ctrl, Shift) — тогда правка поля применяется ко всем.

В середине — все кадры записи (не только диапазон) в одном из видов 1–5 (app/frame_view.py), шкала кадров
с подсветкой диапазона, кнопки «Начало = этот кадр», «Конец = этот кадр» и «Центр затравки» (щелчок по
кадру -> c_x, c_y, px с 1). Номер кадра — номер файла записи, как start_frame и fin_frame в таблице.

Справа — две вкладки. «Таблица»: все колонки таблицы (набор — из заголовков, описание колонки — во
всплывающей подсказке). У пустого поля — кнопка-подсказка «← значение» предыдущей заполненной записи
(принять — нажать её или Enter в пустом поле); «Как у предыдущих» — заполнить пустые поля колонками,
одинаковыми во всех заполненных записях. rim — поле и ползунок: кадр сразу пересчитывается с новым rim
(виды 2–5). «Настройки»: значения settings.py и thresholds.py эксперимента (app/params_page.py) — правка
тоже сразу пересчитывает кадр.

Правки хранятся в памяти (lib/common/table_edit.py) и пишутся в xlsx только по «Сохранить в таблицу»
(Ctrl+S): только изменённые ячейки; перед первой записью за сеанс — копия таблицы в
<OUTPUT_DIR>/table_backups.

Клавиши: ← → кадр (держать — быстро), Пробел — слайдшоу, Home End — первый / последний файл,
PgUp PgDn — запись, 1–5 — вид, M — капли MATLAB, Esc — отменить «центр затравки».
"""

import logging
import math
import time
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QCheckBox, QFormLayout, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea, QSlider,
                               QSplitter, QTableWidget, QTableWidgetItem, QTabWidget, QToolButton, QVBoxLayout,
                               QWidget)

from app.frame_view import PIV_VIEWS, FrameComputer, FrameScale, FrameView
from app.params_page import CHANGED_STYLE, INVALID_STYLE, ParamsPanel
from lib.common import files
from lib.common.records import Record
from lib.common.table_edit import EditableTable, TableSaveError, format_value, same_value
from lib.st1.preview import COMPUTED_VIEWS, VIEWS

log = logging.getLogger(__name__)

VIEW_KEYS = {"1": "frame", "2": "clean", "3": "droplets", "4": "piv", "5": "piv_matlab"}
VIEW_BUTTONS = {"frame": "1 кадр", "clean": "2 без рима", "droplets": "3 капли", "piv": "4 PIV",
                "piv_matlab": "5 PIV как в MATLAB"}
POLL_MS = 100  # как часто забирать посчитанные в фоне кадры
SLIDESHOW_MS = 40  # шаг слайдшоу: до 25 кадров/с (если кадры готовы)
RIM_DELAY_MS = 200  # после правки rim или настроек кадр пересчитывается через эту паузу (пока двигают ползунок — ждёт)
HOLD_STEP_S = 0.15  # держат стрелку, а кадр ещё считается: следующий шаг не раньше чем через столько
LOOKAHEAD = 10  # сколько следующих кадров считать заранее (кроме всего диапазона записи)
BACKUP_FOLDER = "table_backups"  # копии таблицы перед сохранением — в этой папке внутри OUTPUT_DIR
RESULT_FILES = {"st1": ("st1_dir", "particles.mat"), "st2": ("st2_dir", "ptv_unfiltered.mat"),
                "st4": ("st4_dir", "_all_stat.dat")}
RIM_SLIDER_STEPS = 1000  # ползунок rim: логарифмическая шкала от 10 до 100 000 px²
HELP = ("← →  кадр (держать — быстро)    Пробел  слайдшоу    Home End  первый / последний файл    "
        "PgUp PgDn  запись    1–5  вид    M  капли MATLAB    колесо мыши — увеличение, перетаскивание — сдвиг")


def rim_to_slider(rim: float) -> int:
    """rim, px² -> положение ползунка (логарифмическая шкала от 10 до 100 000)."""
    return round(RIM_SLIDER_STEPS * (math.log10(min(max(rim, 10.0), 1e5)) - 1) / 4)


def slider_to_rim(position: int) -> int:
    """Положение ползунка -> rim, px², округлённый до двух значащих цифр."""
    return int(float(f"{10 ** (1 + 4 * position / RIM_SLIDER_STEPS):.2g}"))


class Field(QWidget):
    """Поле одной колонки таблицы: строка ввода и кнопка-подсказка «← значение» (принять одним нажатием).
    header — заголовок колонки; description — её описание из 2-й строки таблицы."""

    def __init__(self, header: str, description: str) -> None:
        super().__init__()
        self.header, self.description = header, description or header
        self.edit = QLineEdit()
        self.edit.setToolTip(self.description)
        self.hint = QToolButton()
        self.hint.setVisible(False)
        self.hint.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.edit, 1)
        layout.addWidget(self.hint)

    def show_value(self, text: str | None, placeholder: str, changed: bool, hint: str | None, hint_tip: str) -> None:
        """Показать значение (None — не трогать набираемый текст), отметку правки и подсказку."""
        if text is not None and self.edit.text() != text:
            self.edit.setText(text)
        self.edit.setPlaceholderText(placeholder)
        self.mark(changed)
        self.hint.setVisible(hint is not None)
        if hint is not None:
            self.hint.setText("← " + (hint if len(hint) <= 18 else hint[:17] + "…"))
            self.hint.setToolTip(hint_tip)

    def mark(self, changed: bool, error: str = "") -> None:
        """Подсветка поля: жёлтым — несохранённая правка, красным — ошибка ввода (текст — в подсказке)."""
        self.edit.setStyleSheet(INVALID_STYLE if error else CHANGED_STYLE if changed else "")
        self.edit.setToolTip(f"{error}\n\n{self.description}" if error else self.description)


class RecordsPage(QWidget):
    """Вкладка «Записи». settings, thresholds — модули эксперимента (или объекты с теми же именами);
    workers — число фоновых процессов расчёта кадров (по умолчанию все ядра, кроме одного; 0 — без них)."""

    changed = Signal()  # изменились несохранённые правки (окно обновит заголовок)
    message = Signal(str)  # сообщение для строки состояния

    def __init__(self, settings: ModuleType, thresholds: ModuleType, workers: int | None = None) -> None:
        super().__init__()
        self.settings, self.thresholds = settings, thresholds
        self.table: EditableTable | None = None
        self.i = -1  # текущая запись (строка таблицы, с 0); её кадры показываются
        self.selected: list[int] = []  # выделенные записи: правка поля — для всех них
        self.frames: files.Frames | None = None
        self.frames_error = ""
        self.number = 1  # текущий кадр — номер файла записи, с 1
        self.view_name = "frame"
        self.show_matlab = True
        self.matlab: tuple[list[np.ndarray], list[np.ndarray]] | None = None  # капли MATLAB по кадрам диапазона
        self.can_compute = False  # заданы кадры и rim (без них виды 2–5 не считаются)
        self.waiting = False  # показан исходный кадр, потому что нужный вид ещё считается
        self.wanted: list[int] = []  # кадры, поставленные в фоновый расчёт
        self.rest_pending = False  # кадры впрок поставить в расчёт, когда посчитается текущий
        self.info_text, self.info_error = "", False
        self.last_step = 0.0
        self.backed_up: set[Path] = set()  # таблицы, копия которых в этом сеансе уже сделана
        self.results_present: dict[int, dict[str, bool]] = {}
        self.frame_cache: dict[int, np.ndarray] = {}
        self.fields: dict[str, Field] = {}
        self.rim_slider: QSlider | None = None
        self.computer = FrameComputer(workers)
        self._build()
        self._make_shortcuts()
        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(POLL_MS)
        self.poll_timer.timeout.connect(self._poll)
        self.poll_timer.start()
        self.slideshow_timer = QTimer(self)
        self.slideshow_timer.setInterval(SLIDESHOW_MS)
        self.slideshow_timer.timeout.connect(self._slideshow_step)
        self.rim_timer = QTimer(self)
        self.rim_timer.setSingleShot(True)
        self.rim_timer.setInterval(RIM_DELAY_MS)
        self.rim_timer.timeout.connect(self.recompute)
        self.params_timer = QTimer(self)
        self.params_timer.setSingleShot(True)
        self.params_timer.setInterval(RIM_DELAY_MS)
        self.params_timer.timeout.connect(self.apply_parameters)
        self.load(settings, thresholds)

    # ------------------------------------------------------------------ разметка

    def _build(self) -> None:
        """Разметка вкладки: список записей | кадр | поля таблицы."""
        self.list = QTableWidget(0, 5)
        self.list.setHorizontalHeaderLabels(["Запись", "Готова", "st1", "st2", "st4"])
        self.list.verticalHeader().setVisible(False)
        self.list.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        header = self.list.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, 5):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.list.currentCellChanged.connect(lambda row, *_: self._current_changed(row))
        self.list.itemSelectionChanged.connect(self._selection_changed)

        def button(text: str, slot: Any = None, tip: str = "", checkable: bool = False) -> QPushButton:
            b = QPushButton(text)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # чтобы Пробел и стрелки не нажимали кнопки
            b.setToolTip(tip)
            b.setCheckable(checkable)
            if slot is not None:
                b.clicked.connect(lambda checked=False: slot())
            return b

        self.title = QLabel()
        bold = QFont()
        bold.setBold(True)
        self.title.setFont(bold)
        self.info = QLabel()
        self.info.setWordWrap(True)
        self.view = FrameView()
        self.view.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.view.clicked.connect(self._frame_clicked)
        self.scale = FrameScale()
        self.scale.chosen.connect(self.go)

        self.play_button = button("▶ Слайдшоу", self.toggle_slideshow, "Пробел")
        self.frame_label = QLabel()
        self.start_button = button("Начало = этот кадр", lambda: self._set_frame_column("start_frame"),
                                   "start_frame = номер текущего файла")
        self.fin_button = button("Конец = этот кадр", lambda: self._set_frame_column("fin_frame"),
                                 "fin_frame = номер текущего файла")
        self.center_button = button("Центр затравки", None, "Нажать, затем щёлкнуть по кадру — c_x, c_y "
                                    "выделенных записей (Esc — отмена)", checkable=True)
        self.center_button.toggled.connect(self._center_mode)
        navigation = QHBoxLayout()
        for text, slot, tip in (("⏮", lambda: self.go(1), "первый файл (Home)"),
                                ("◀", lambda: self.step(-1), "предыдущий кадр (←)")):
            navigation.addWidget(button(text, slot, tip))
        navigation.addWidget(self.play_button)
        for text, slot, tip in (("▶", lambda: self.step(1), "следующий кадр (→)"),
                                ("⏭", lambda: self.go(self.count), "последний файл (End)")):
            navigation.addWidget(button(text, slot, tip))
        navigation.addWidget(self.frame_label)
        navigation.addStretch(1)
        for b in (self.start_button, self.fin_button, self.center_button):
            navigation.addWidget(b)

        views = QHBoxLayout()
        self.view_buttons: dict[str, QPushButton] = {}
        group = QButtonGroup(self)
        for key, name in VIEW_KEYS.items():
            b = button(VIEW_BUTTONS[name], lambda v=name: self.set_view(v), f"{VIEWS[name]} (клавиша {key})",
                       checkable=True)
            group.addButton(b)
            views.addWidget(b)
            self.view_buttons[name] = b
        self.matlab_box = QCheckBox("капли MATLAB (M)")
        self.matlab_box.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.matlab_box.setChecked(True)
        self.matlab_box.toggled.connect(self._matlab_toggled)
        views.addWidget(self.matlab_box)
        views.addStretch(1)
        views.addWidget(button("Весь кадр", self.view.fit, "показать кадр целиком"))
        hint = QLabel(HELP)
        hint.setStyleSheet("color: #707070;")
        hint.setWordWrap(True)

        center = QWidget()
        column = QVBoxLayout(center)
        column.setContentsMargins(4, 0, 4, 0)
        column.addWidget(self.title)
        column.addWidget(self.info)
        column.addWidget(self.view, 1)
        column.addWidget(self.scale)
        column.addLayout(navigation)
        column.addLayout(views)
        column.addWidget(hint)

        self.form_title = QLabel()
        self.form_title.setFont(bold)
        self.form_title.setWordWrap(True)
        self.form_note = QLabel()
        self.form_note.setWordWrap(True)
        self.form = QFormLayout()
        self.like_button = button("Как у предыдущих", self.fill_like_previous)
        self.save_button = button("Сохранить в таблицу (Ctrl+S)", self.save)
        self.changes_label = QLabel()
        panel = QWidget()
        side = QVBoxLayout(panel)
        side.addWidget(self.form_title)
        side.addWidget(self.form_note)
        side.addLayout(self.form)
        side.addWidget(self.like_button)
        side.addSpacing(12)
        side.addWidget(self.save_button)
        side.addWidget(self.changes_label)
        side.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(panel)
        self.params = ParamsPanel()
        self.params.edited.connect(lambda: self.params_timer.start())
        self.params.changed.connect(self.changed.emit)
        self.params.message.connect(self.message.emit)
        self.side = QTabWidget()
        self.side.addTab(scroll, "Таблица")
        self.side.addTab(self.params, "Настройки")
        self.side.setTabToolTip(0, "поля таблицы записей выделенных записей")
        self.side.setTabToolTip(1, "settings.py и thresholds.py эксперимента: правка сразу пересчитывает кадр")

        self.list.setMinimumWidth(330)
        self.side.setMinimumWidth(360)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.list)
        splitter.addWidget(center)
        splitter.addWidget(self.side)
        splitter.setStretchFactor(1, 1)
        splitter.setChildrenCollapsible(False)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(splitter)
        self.view_buttons[self.view_name].setChecked(True)

    def _make_shortcuts(self) -> None:
        """Клавиши вкладки. В поле ввода цифры, стрелки, Home, End и Пробел работают как обычно."""
        keys = {"Right": lambda: self.step(1), "Left": lambda: self.step(-1), "Space": self.toggle_slideshow,
                "Home": lambda: self.go(1), "End": lambda: self.go(self.count),
                "PgDown": lambda: self.switch_record(1), "PgUp": lambda: self.switch_record(-1),
                "M": lambda: self.matlab_box.toggle(), "Esc": lambda: self.center_button.setChecked(False)}
        keys.update({key: (lambda v=view: self.set_view(v)) for key, view in VIEW_KEYS.items()})
        for key, slot in keys.items():
            QShortcut(QKeySequence(key), self).activated.connect(slot)

    def _build_form(self) -> None:
        """Поля всех колонок таблицы (после смены таблицы); под rim — ползунок."""
        while self.form.rowCount():
            self.form.removeRow(0)
        self.fields, self.rim_slider = {}, None
        if self.table is None:
            return
        rim_header = self.table.column("rim")
        for header in self.table.headers:
            field = Field(header, self.table.descriptions.get(header, ""))
            field.edit.textEdited.connect(lambda text, h=header: self._field_edited(h, text))
            field.edit.returnPressed.connect(lambda h=header: self._accept_suggestion(h))
            field.hint.clicked.connect(lambda checked=False, h=header: self._accept_suggestion(h))
            label = QLabel(header)
            label.setToolTip(field.description)
            self.form.addRow(label, field)
            self.fields[header] = field
            if header == rim_header:
                self.rim_slider = QSlider(Qt.Orientation.Horizontal)
                self.rim_slider.setRange(0, RIM_SLIDER_STEPS)
                self.rim_slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                self.rim_slider.setToolTip("rim — порог площади, px²: объекты крупнее закрашиваются как рим.\n"
                                           "Вправо — порог больше, закрашивается меньше. Шкала 10…100 000.")
                self.rim_slider.valueChanged.connect(self._rim_slider_moved)
                self.form.addRow("", self.rim_slider)

    # ------------------------------------------------------------------ эксперимент и таблица

    def load(self, settings: ModuleType, thresholds: ModuleType) -> None:
        """Открыть эксперимент или перечитать его настройки и таблицу (модули settings и thresholds).
        Несохранённые правки таблицы и настроек тех же файлов остаются (таблица тогда не перечитывается)."""
        self.params.load(settings, thresholds)
        self._open_experiment(*self.params.working())

    def apply_parameters(self) -> None:
        """Применить правки панели «Настройки» (после короткой паузы): пересчитать кадр; если поменялись пути —
        перечитать таблицу, кадры записи, отметки результатов."""
        settings, thresholds = self.params.working()
        old, self.settings, self.thresholds = self.settings, settings, thresholds
        if Path(settings.TABLE_FILE) != Path(old.TABLE_FILE):
            if self.has_changes:
                self.message.emit("Таблица не сменена: в ней несохранённые правки — сначала сохраните их")
            else:
                self._open_experiment(settings, thresholds)
            return
        paths = ("IMAGES_DIR", "OUTPUT_DIR", "MATLAB_RESULTS_DIR")
        if any(getattr(settings, name, None) != getattr(old, name, None) for name in paths) and self.i >= 0:
            self._fill_list()
            self.open_record(self.i, self.number)
            return
        self.recompute()

    def _open_experiment(self, settings: Any, thresholds: Any) -> None:
        """Таблица, список, поля и текущая запись для настроек settings и порогов thresholds. Если в той же
        таблице есть несохранённые правки, она не перечитывается."""
        self.settings, self.thresholds = settings, thresholds
        path = Path(settings.TABLE_FILE)
        same_table = self.table is not None and self.table.path == path
        old_i, old_number = self.i, self.number
        if not (same_table and self.table.changes):
            try:
                self.table = EditableTable(path)
            except Exception as error:
                log.exception("Не удалось прочитать таблицу")
                self.table = None
                self.message.emit(f"Не удалось прочитать таблицу {path}: {error}")
        self._build_form()
        self._fill_list()
        if self.table is None or not len(self.table):
            self.i, self.selected, self.frames = -1, [], None
            self.frames_error = "в таблице нет записей" if self.table is not None else "таблица не прочитана"
            self.draw()
            self._fill_form()
            return
        i = min(max(old_i, 0), len(self.table) - 1) if same_table else 0
        self._select(i, old_number if same_table and i == old_i else None)

    def _select(self, i: int, number: int | None = None) -> None:
        """Выделить одну запись i и открыть её (на кадре number, по умолчанию — начало диапазона)."""
        self.list.blockSignals(True)
        self.list.clearSelection()
        self.list.setCurrentCell(i, 0)
        self.list.selectRow(i)
        self.list.blockSignals(False)
        self.open_record(i, number)
        self._selection_changed()

    def _fill_list(self) -> None:
        """Список записей: отметки «готова», результаты st1, st2, st4."""
        self.list.blockSignals(True)
        n = len(self.table) if self.table is not None else 0
        self.list.setRowCount(n)
        self.results_present = {}
        for i in range(n):
            record = self.table.record(i)
            output = Path(self.settings.OUTPUT_DIR)
            self.results_present[i] = {
                stage: record is not None and (getattr(record, folder)(output) / name).exists()
                for stage, (folder, name) in RESULT_FILES.items()}
            self._update_row(i)
        self.list.blockSignals(False)

    @staticmethod
    def readiness(record: Record | None) -> str:
        """Готова ли запись к обработке: «✓» или чего не хватает."""
        if record is None:
            return "нет пути"
        missing = [what for what, ok in (("кадры", record.has_frames), ("rim", record.rim is not None),
                                         ("центр", record.cx is not None and record.cy is not None)) if not ok]
        return "нет: " + ", ".join(missing) if missing else "✓"

    def _update_row(self, i: int) -> None:
        """Строка списка записей i (после правки)."""
        record = self.table.record(i)
        changed = self.table.changed(i)
        ready = self.readiness(record)
        texts = [self.table.name(i) + (" *" if changed else ""), ready,
                 *("✓" if self.results_present.get(i, {}).get(stage) else "" for stage in RESULT_FILES)]
        for column, text in enumerate(texts):
            item = self.list.item(i, column)
            if item is None:
                item = QTableWidgetItem()
                self.list.setItem(i, column, item)
            item.setText(text)
            if column > 0:
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        name = self.list.item(i, 0)
        font = name.font()
        font.setBold(changed)
        name.setFont(font)
        name.setToolTip(f"{self.table.name(i)}\nпапка кадров: {record.path if record else '—'}")
        self.list.item(i, 1).setForeground(QBrush(QColor("#1a8f1a" if ready == "✓" else "#808080")))

    def refresh_results(self) -> None:
        """Заново проверить, есть ли у записей результаты st1, st2, st4 (после обработки)."""
        self._fill_list()

    @property
    def has_changes(self) -> bool:
        """Есть ли несохранённые правки таблицы."""
        return self.table is not None and bool(self.table.changes)

    def discard(self) -> None:
        """Забыть несохранённые правки (таблица перечитается при следующем load)."""
        if self.table is not None:
            self.table.changes.clear()
        self.changed.emit()

    def save(self) -> bool:
        """Записать правки в таблицу (только изменённые ячейки); перед первой записью за сеанс — копия
        таблицы в <OUTPUT_DIR>/table_backups. True — записано или нечего записывать."""
        if not self.has_changes:
            self.message.emit("Нет несохранённых правок")
            return True
        path = self.table.path
        backup_dir = None if path in self.backed_up else Path(self.settings.OUTPUT_DIR) / BACKUP_FOLDER
        try:
            backup = self.table.save(backup_dir)
        except TableSaveError as error:
            QMessageBox.warning(self, "Таблица не сохранена", str(error))
            return False
        except Exception as error:
            log.exception("Не удалось сохранить таблицу")
            QMessageBox.critical(self, "Таблица не сохранена", f"{type(error).__name__}: {error}")
            return False
        if backup is not None:
            self.backed_up.add(path)
        rows = len(self.table)
        self._fill_list()
        if self.i >= rows:
            self._select(rows - 1)
        else:
            self._selection_changed()
        self.changed.emit()
        self.message.emit(f"Сохранено в {path.name}" + (f"; копия прежней таблицы — {backup}" if backup else ""))
        return True

    # ------------------------------------------------------------------ записи и поля

    def _current_changed(self, row: int) -> None:
        """Щелчок или стрелки в списке: открыть запись row."""
        if row >= 0 and row != self.i:
            self.open_record(row)

    def _selection_changed(self) -> None:
        """Изменилось выделение в списке: поля показывают выделенные записи."""
        rows = sorted({index.row() for index in self.list.selectionModel().selectedRows()})
        self.selected = rows or ([self.i] if self.i >= 0 else [])
        self._fill_form()

    def switch_record(self, step: int) -> None:
        """Предыдущая (step = −1) или следующая (+1) запись списка."""
        if self.table is not None and 0 <= self.i + step < len(self.table):
            self._select(self.i + step)

    def _fill_form(self) -> None:
        """Поля: значения выделенных записей (у разных значений — пустое поле «разные»), кнопки."""
        rows = self.selected
        single = len(rows) == 1
        if self.table is None or not rows:
            self.form_title.setText("")
            self.form_note.setText("")
        else:
            self.form_title.setText(self.table.name(rows[0]) if single else f"Выделено записей: {len(rows)}")
            self.form_note.setText("" if single else "Правка поля применяется ко всем выделенным записям.")
            for header in self.fields:
                self._show_field(header)
            self._sync_rim_slider()
            common = self.table.common_values(exclude=rows[0] if single else None)
            self.like_button.setToolTip(
                "Заполнить пустые поля значениями, одинаковыми во всех заполненных записях:\n"
                + (", ".join(f"{h} = {format_value(v)}" for h, v in common.items()) if common
                   else "таких нет (нужны хотя бы две заполненные записи)"))
        frames_ok = self.table is not None and self.count > 0 and self.i in rows
        for key, b in (("start_frame", self.start_button), ("fin_frame", self.fin_button)):
            b.setEnabled(frames_ok and single and self.table.column(key) is not None)
        self.center_button.setEnabled(frames_ok and self.table.column("cx") is not None
                                      and self.table.column("cy") is not None)
        self.like_button.setEnabled(bool(rows))
        self._update_counts()

    def _show_field(self, header: str, keep_text: bool = False) -> None:
        """Поле колонки header: значение выделенных записей, отметка правки, подсказка.
        keep_text — не трогать текст (его сейчас набирают)."""
        rows = self.selected
        values = [self.table.value(i, header) for i in rows]
        same = all(same_value(v, values[0]) for v in values)
        changed = any(self.table.changed(i, header) for i in rows)
        hint, tip = None, ""
        suggestion = self.table.suggestion(rows[0], header) if len(rows) == 1 else None
        if suggestion is not None:
            j, value = suggestion
            hint = format_value(value)
            tip = f"Как у записи {self.table.name(j)}: {hint}.\nНажмите (или Enter в пустом поле), чтобы принять."
        text = None if keep_text else (format_value(values[0]) if same else "")
        self.fields[header].show_value(text, "" if same else "разные", changed, hint, tip)

    def _update_counts(self) -> None:
        """Подпись «несохранённых правок» и доступность кнопки «Сохранить»."""
        n = len(self.table.changes) if self.table is not None else 0
        self.changes_label.setText(f"Несохранённых правок: {n}" if n else "Все правки сохранены")
        self.save_button.setEnabled(n > 0)

    def _field_edited(self, header: str, text: str) -> None:
        """Пользователь набрал текст в поле: правка для всех выделенных записей (ошибка — красным)."""
        try:
            value = self.table.parse(header, text)
        except ValueError as error:
            self.fields[header].mark(False, str(error))
            return
        self.set_value(header, value, keep_text=True)

    def _accept_suggestion(self, header: str) -> None:
        """Принять подсказку «← значение» для пустого поля (одна выделенная запись)."""
        if len(self.selected) == 1:
            suggestion = self.table.suggestion(self.selected[0], header)
            if suggestion is not None:
                self.set_value(header, suggestion[1])

    def set_value(self, header: str, value: Any, rows: list[int] | None = None, keep_text: bool = False) -> None:
        """Правка колонки header у записей rows (по умолчанию — у выделенных); кадр и список обновляются."""
        rows = self.selected if rows is None else rows
        for i in rows:
            self.table.set(i, header, value)
            self._update_row(i)
        if header in self.fields and rows == self.selected:
            self._show_field(header, keep_text)
        self._update_counts()
        self.changed.emit()
        if self.i in rows:
            self._current_values_changed(header)

    def fill_like_previous(self) -> None:
        """«Как у предыдущих»: пустые поля выделенных записей — значениями, одинаковыми во всех заполненных."""
        filled: set[str] = set()
        for i in self.selected:
            filled.update(self.table.fill_like_previous(i))
            self._update_row(i)
        self._fill_form()
        self.changed.emit()
        if self.i in self.selected:
            for header in filled:
                self._current_values_changed(header)
        self.message.emit(f"Заполнено: {', '.join(sorted(filled))}" if filled else "Заполнять нечего")

    def _current_values_changed(self, header: str) -> None:
        """Правка значения текущей записи: обновить кадры, шкалу, крестик или пересчитать с новым rim."""
        t = self.table
        if header in (t.column("rec_name"), t.column("images_path_side")):
            self.open_record(self.i, self.number)
        elif header == t.column("rim"):
            if self.rim_slider is not None and not self.rim_slider.isSliderDown():
                self._sync_rim_slider()
            self.rim_timer.start()  # пересчёт после паузы
        elif header in (t.column("start_frame"), t.column("fin_frame")):
            self._update_scale()
            self._prefetch()
        elif header in (t.column("cx"), t.column("cy")) and self.frames is not None:
            record = t.record(self.i)
            self.view.set_center(record.cx, record.cy)

    def _sync_rim_slider(self) -> None:
        """Ползунок rim — по значению rim выделенных записей."""
        header = self.table.column("rim") if self.table is not None else None
        if self.rim_slider is None or header is None or not self.selected:
            return
        values = [self.table.value(i, header) for i in self.selected]
        if isinstance(values[0], (int, float)) and all(same_value(v, values[0]) for v in values):
            self.rim_slider.blockSignals(True)
            self.rim_slider.setValue(rim_to_slider(values[0]))
            self.rim_slider.blockSignals(False)

    def _rim_slider_moved(self, position: int) -> None:
        """Ползунок rim сдвинут: rim выделенных записей (две значащие цифры)."""
        self.set_value(self.table.column("rim"), slider_to_rim(position))

    def _set_frame_column(self, key: str) -> None:
        """«Начало / конец = этот кадр»: start_frame или fin_frame текущей записи = номер текущего файла."""
        header = self.table.column(key) if self.table is not None else None
        if header is None or not self.count:
            return
        self.set_value(header, self.number, [self.i])
        record = self.table.record(self.i)
        if record and record.start_frame and record.fin_frame and record.fin_frame < record.start_frame:
            self.message.emit("Внимание: начало диапазона позже конца")

    def _center_mode(self, on: bool) -> None:
        """Режим «центр затравки»: курсор-крестик; следующий щелчок по кадру задаёт c_x, c_y."""
        self.view.viewport().setCursor(Qt.CursorShape.CrossCursor if on else Qt.CursorShape.ArrowCursor)
        if on:
            self.message.emit("Щёлкните по кадру в центре затравки (Esc — отмена)")

    def _frame_clicked(self, x: float, y: float) -> None:
        """Щелчок по кадру: в режиме «центр затравки» — c_x, c_y выделенных записей (px с 1, целые)."""
        if not self.center_button.isChecked():
            return
        cx, cy = round(x), round(y)
        self.set_value(self.table.column("cx"), cx)
        self.set_value(self.table.column("cy"), cy)
        self.center_button.setChecked(False)
        self.message.emit(f"Центр затравки: {self.table.column('cx')} = {cx}, {self.table.column('cy')} = {cy}")

    # ------------------------------------------------------------------ кадры

    @property
    def count(self) -> int:
        """Число файлов (кадров) текущей записи."""
        return len(self.frames) if self.frames is not None else 0

    def open_record(self, i: int, number: int | None = None) -> None:
        """Открыть запись i: её кадры (на кадре number, по умолчанию — начало диапазона), капли MATLAB."""
        self.stop_slideshow()
        self.i = i
        self.frames, self.frames_error, self.matlab = None, "", None
        self.frame_cache.clear()
        record = self.table.record(i)
        if record is None:
            self.frames_error = "в таблице не задан rec_name или images_path_side"
        else:
            try:
                self.frames = files.Frames(record.images_dir(Path(self.settings.IMAGES_DIR)))
            except Exception as error:  # нет папки, нет картинок — окно не закрывается
                self.frames_error = str(error)
            self.matlab = self._load_matlab(record)
        if number is None:
            number = record.start_frame if record is not None and record.start_frame else 1
        self.number = min(max(number, 1), max(self.count, 1))
        self._configure_computer()
        self.draw()
        self._prefetch()

    def _load_matlab(self, record: Record) -> tuple[list[np.ndarray], list[np.ndarray]] | None:
        """Капли MATLAB записи по кадрам (если задан MATLAB_RESULTS_DIR и там есть particles.mat)."""
        folder = getattr(self.settings, "MATLAB_RESULTS_DIR", None)
        if not folder or not (record.st1_dir(Path(folder)) / "particles.mat").exists():
            return None
        try:
            centers, radii, _, _ = files.load_particles(record.st1_dir(Path(folder)))
        except Exception:
            log.exception("Не удалось прочитать капли MATLAB")
            return None
        return centers, radii

    def _matlab_at(self, number: int) -> tuple[np.ndarray, np.ndarray] | None:
        """Капли MATLAB на кадре number, если показ включён и у MATLAB столько же кадров, сколько в диапазоне."""
        record = self.table.record(self.i) if self.table is not None else None
        if self.matlab is None or not self.show_matlab or record is None or not record.has_frames:
            return None
        centers, radii = self.matlab
        k = number - record.start_frame
        if len(centers) != record.fin_frame - record.start_frame + 1 or not 0 <= k < len(centers):
            return None
        return centers[k], radii[k]

    def _configure_computer(self) -> None:
        """Передать расчёту параметры текущей записи (при смене rim или порогов посчитанное забывается)."""
        record = self.table.record(self.i) if self.table is not None and self.i >= 0 else None
        self.can_compute = self.frames is not None and record is not None and record.rim is not None
        if not self.can_compute:
            self.computer.cancel()
            return
        s, t = self.settings, self.thresholds
        values = {name: getattr(t, name) for name in dir(t) if name.isupper()}
        self.computer.configure(str(record.images_dir(Path(s.IMAGES_DIR))), self.count, s.BACKGROUND_FRAME,
                                record.rim, s.SURFACE_ROW, s.ERASE_REGIONS, values)

    def recompute(self) -> None:
        """Пересчитать кадры текущей записи с текущими rim, настройками и порогами (после их правки — сам,
        с паузой). Если показан исходный кадр — переключиться на «без рима», чтобы было видно закрашенное."""
        self._configure_computer()
        if self.view_name == "frame" and self.can_compute:
            self.set_view("clean")
        else:
            self.draw()
            self._prefetch()

    def _frame(self, number: int) -> np.ndarray:
        """Исходный кадр number (несколько последних запоминаются)."""
        if number not in self.frame_cache:
            if len(self.frame_cache) >= 4:
                self.frame_cache.pop(next(iter(self.frame_cache)))
            self.frame_cache[number] = self.frames.gray(number)
        return self.frame_cache[number]

    def draw(self) -> None:
        """Показать текущий кадр в текущем виде; обновить подписи и шкалу."""
        self.waiting = False
        record = self.table.record(self.i) if self.table is not None and self.i >= 0 else None
        if self.frames is None:
            self.view.clear_frame()
            self.title.setText(self.table.name(self.i) if record is not None else "")
            self._set_info(f"Кадры не показать: {self.frames_error}", True)
            self._update_scale()
            return
        view, text, error = self.view_name, "", False
        try:
            frame = self._frame(self.number)
            result = None
            if view in COMPUTED_VIEWS:
                if not self.can_compute:
                    text, error = "Для этого вида нужен rim (поле справа)", True
                elif self.number in self.computer.errors:
                    text, error = f"Ошибка расчёта: {self.computer.errors[self.number]}", True
                else:
                    result = self.computer.result(self.number, view in PIV_VIEWS)
                    if result is None:
                        text, self.waiting = "кадр считается…", True
            caption = self.view.show(
                view if result is not None else "frame", frame, result, matlab=self._matlab_at(self.number),
                arrow_scale=self.thresholds.PIV_PLOT_ARROW_SCALE, speed_max=self.thresholds.PIV_PLOT_SPEED_MAX,
                mm_per_px=self.settings.SCALE_MM_PER_PX, fps=self.settings.FPS)
            text = text or caption
        except Exception as e:  # ошибка расчёта или чтения кадра — показать, окно не закрывать
            log.exception("Ошибка показа кадра")
            text, error = f"Ошибка: {e}", True
        self.view.set_center(record.cx, record.cy)
        self.title.setText(f"{record.name} — кадр {self.number} из {self.count} "
                           f"(файл {self.frames.paths[self.number - 1].name}) — {VIEWS[view]}")
        self._set_info(text, error)
        self._update_scale()

    def _set_info(self, text: str, error: bool = False) -> None:
        """Подпись под заголовком: что на кадре (+ сколько посчитано в фоне); ошибки — красным."""
        self.info_text, self.info_error = text, error
        if self.computer.busy() and self.wanted:
            with_piv = self.view_name in PIV_VIEWS
            done = sum(self.computer.ready(n, with_piv) for n in self.wanted)
            text = f"{text} — в фоне посчитано {done} из {len(self.wanted)}" if text else \
                f"в фоне посчитано {done} из {len(self.wanted)}"
        self.info.setText(text)
        self.info.setStyleSheet("color: #c00000;" if error else "")

    def _update_scale(self) -> None:
        """Шкала кадров и подпись «кадр N из M»."""
        record = self.table.record(self.i) if self.table is not None and self.i >= 0 else None
        computed = set(self.computer.results) if self.can_compute else set()
        self.scale.set_state(self.count, self.number, record.start_frame if record else None,
                             record.fin_frame if record else None, computed)
        self.frame_label.setText(f"  кадр {self.number} из {self.count}" if self.count else "  нет кадров")

    def _prefetch(self) -> None:
        """Поставить в расчёт текущий кадр, а когда он готов, — LOOKAHEAD следующих и весь диапазон записи
        (пока текущий не готов, кадры впрок не мешают ему считаться)."""
        self.wanted, self.rest_pending = [], False
        if not self.can_compute or self.view_name not in COMPUTED_VIEWS:
            return
        n, record, with_piv = self.number, self.table.record(self.i), self.view_name in PIV_VIEWS
        if not self.computer.ready(n, with_piv) and n not in self.computer.errors:
            self.computer.cancel_waiting()
            self.computer.focus([n], with_piv)
            self.wanted, self.rest_pending = [n], True
            return
        wanted = [n, *range(n + 1, min(n + LOOKAHEAD, self.count) + 1)]
        if record.has_frames:
            first, last = max(record.start_frame, 1), min(record.fin_frame, self.count)
            wanted += [k for k in (*range(n + 1, last + 1), *range(first, n)) if first <= k <= last]
        self.wanted = list(dict.fromkeys(wanted))
        self.computer.focus(self.wanted, with_piv)

    def _poll(self) -> None:
        """Таймер: забрать посчитанные в фоне кадры; дождались текущего — нарисовать."""
        if not self.computer.collect(self.number):
            return
        ready = self.computer.ready(self.number, self.view_name in PIV_VIEWS) or self.number in self.computer.errors
        if ready and self.rest_pending:
            self._prefetch()  # текущий готов — теперь кадры впрок
        if self.waiting and ready:
            self.draw()
        else:
            self._update_scale()
            self._set_info(self.info_text, self.info_error)

    def go(self, number: int) -> None:
        """Показать кадр number (номер файла, с 1)."""
        if not self.count:
            return
        number = min(max(int(number), 1), self.count)
        if number != self.number:
            self.number = number
            self.draw()
            self._prefetch()

    def step(self, direction: int) -> None:
        """Кадр назад (−1) или вперёд (+1). Если стрелку держат, а кадр ещё считается, — не убегать вперёд."""
        self.stop_slideshow()
        if self.waiting and time.monotonic() - self.last_step < HOLD_STEP_S:
            return
        self.last_step = time.monotonic()
        self.go(self.number + direction)

    def set_view(self, view: str) -> None:
        """Вид кадра: frame, clean, droplets, piv, piv_matlab (клавиши 1–5)."""
        self.view_name = view
        self.view_buttons[view].setChecked(True)
        self.draw()
        self._prefetch()

    def _matlab_toggled(self, on: bool) -> None:
        """Капли MATLAB показать / скрыть (клавиша M)."""
        self.show_matlab = on
        self.draw()

    def toggle_slideshow(self) -> None:
        """Слайдшоу пуск / стоп (Пробел); с последнего кадра — с начала."""
        if self.slideshow_timer.isActive():
            self.stop_slideshow()
        elif self.count:
            if self.number >= self.count:
                self.go(1)
            self.slideshow_timer.start()
            self.play_button.setText("⏸ Стоп")

    def stop_slideshow(self) -> None:
        """Остановить слайдшоу."""
        self.slideshow_timer.stop()
        self.play_button.setText("▶ Слайдшоу")

    def _slideshow_step(self) -> None:
        """Шаг слайдшоу: следующий кадр, если он готов (иначе ждём); в конце записи — стоп."""
        following = self.number + 1
        if following > self.count:
            self.stop_slideshow()
            return
        if (self.view_name in COMPUTED_VIEWS and self.can_compute and self.computer.parallel
                and following not in self.computer.errors
                and not self.computer.ready(following, self.view_name in PIV_VIEWS)):
            return  # следующий кадр ещё считается в фоне — ждём
        self.go(following)

    def shutdown(self) -> None:
        """Окно закрывается: остановить таймеры и фоновый расчёт."""
        for timer in (self.poll_timer, self.slideshow_timer, self.rim_timer, self.params_timer):
            timer.stop()
        self.computer.shutdown()
