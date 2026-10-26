"""Панель «Настройки»: все значения settings.py и thresholds.py эксперимента — полями ввода и ползунками.

Панель стоит на вкладке «Записи» справа от кадра (вторая вкладка рядом с «Таблицей»), чтобы пороги
подбирались «на глаз»: правка значения через короткую паузу пересчитывает кадр. Подпись поля — имя
и комментарий из файла (пояснение над строкой — во всплывающей подсказке). У чисел и кортежей из чисел
(например, RIM_GRAY_RANGE) — ползунок на каждое число: от 0 (у отрицательных — от удвоенного значения)
до удвоенного значения; набранное в поле число становится новой серединой ползунка. Строки (пути) вводятся
без кавычек; кортежи и списки — как в файле, например (-100, 0) или [10, 20].

«Сохранить настройки» записывает в файлы только изменённые значения (комментарии и остальное остаются);
«Вернуть как в файлах» — забыть правки. Пока правки не сохранены, скрипты main/ работают со значениями
из файлов. Логика без окон — lib/common/config_edit.py.
"""

import html
import math
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea, QSlider,
                               QVBoxLayout, QWidget)

from lib.common.config_edit import Setting, SettingsFile, literal, value_text

FLOAT_STEPS = 1000  # число шагов ползунка дробного числа
MAX_SLIDERS = 4  # ползунки — у кортежей не длиннее этого
KINDS = {"settings": "settings.py — файлы, съёмка, области st4", "thresholds": "thresholds.py — пороги алгоритмов"}
CHANGED_STYLE = "QLineEdit { background: #fff1b8; }"  # несохранённая правка
INVALID_STYLE = "QLineEdit { background: #ffc9c9; }"  # ошибка ввода


def slider_range(value: float) -> tuple[float, float]:
    """Пределы ползунка числа: от 0 (у отрицательных — от удвоенного значения) до удвоенного значения;
    у целых — не уже 10 в каждую сторону, у нуля — 0…1 (дробные) или 0…10 (целые)."""
    span = float(abs(value)) if value else 1.0
    if isinstance(value, int):
        span = max(span, 10.0)
    low = value - span if value < 0 else max(0.0, value - span)
    return low, value + span


def number_text(value: float, step: float) -> str:
    """Дробное число с точностью шага ползунка, без лишних нулей и без записи вида 1e-07."""
    decimals = max(1, math.ceil(-math.log10(step))) if step > 0 else 6
    text = f"{value:.{decimals}f}".rstrip("0")
    text = text + "0" if text.endswith(".") else text
    return "0.0" if text in ("-0.0", "") else text


def _numbers(value: Any) -> list[float] | None:
    """Числа, которым нужны ползунки: само число или кортеж из 1–MAX_SLIDERS чисел; иначе None."""
    def is_number(v: Any) -> bool:
        return isinstance(v, (int, float)) and not isinstance(v, bool)

    if is_number(value):
        return [value]
    if isinstance(value, tuple) and 0 < len(value) <= MAX_SLIDERS and all(is_number(v) for v in value):
        return list(value)
    return None


class ParamField(QWidget):
    """Одно значение файла настроек: подпись, поле ввода, ползунки для чисел.
    kind — "settings" или "thresholds"; setting — значение из файла (config_edit.Setting)."""

    edited = Signal(str, str, str)  # kind, имя, новый текст значения (как в файле)

    def __init__(self, kind: str, setting: Setting) -> None:
        super().__init__()
        self.kind, self.setting = kind, setting
        self.is_text = isinstance(setting.value, str)
        self.is_tuple = isinstance(setting.value, tuple)
        label = QLabel(f"<b>{setting.name}</b>" + (f" — {html.escape(setting.comment)}" if setting.comment else ""))
        label.setWordWrap(True)
        label.setToolTip(setting.note or setting.comment)
        self.edit = QLineEdit()
        self.edit.setToolTip(setting.note or setting.comment)
        self.edit.textEdited.connect(self._typed)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(2)
        layout.addWidget(label)
        layout.addWidget(self.edit)
        self.sliders: list[QSlider] = []
        self.ranges: list[tuple[float, float]] = []
        self.integers: list[bool] = []
        self.sliding = False  # правка идёт от ползунка: его пределы не пересчитывать, пока его тянут
        numbers = _numbers(setting.value)
        for i, number in enumerate(numbers or []):
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            slider.valueChanged.connect(lambda position, i=i: self._slid(i, position))
            row = QHBoxLayout()
            if len(numbers) > 1:
                row.addWidget(QLabel(f"{i + 1}:"))
            row.addWidget(slider, 1)
            layout.addLayout(row)
            self.sliders.append(slider)
            self.ranges.append((0.0, 1.0))
            self.integers.append(isinstance(number, int))

    def matches(self, words: str) -> bool:
        """Подходит ли поле под поиск (имя, комментарий, пояснение, раздел)."""
        s = self.setting
        return words.lower() in f"{s.name} {s.comment} {s.note} {s.section}".lower()

    def show_text(self, text: str, changed: bool, keep_text: bool = False) -> None:
        """Показать значение (текст литерала из файла или правки), отметку правки, положение ползунков.
        keep_text — не трогать набираемый текст; ползунки при этом встают серединой на новое значение."""
        if not keep_text:
            shown = literal(text) if self.is_text else text
            if self.edit.text() != shown:
                self.edit.setText(shown)
        self.mark(changed)
        numbers = _numbers(literal(text))
        if numbers is not None and len(numbers) == len(self.sliders) and not self.sliding:
            self._place_sliders(numbers, recenter=True)

    def _place_sliders(self, numbers: list[float], recenter: bool) -> None:
        """Ползунки — на числа numbers (recenter — сначала пересчитать пределы вокруг них)."""
        for i, (slider, number) in enumerate(zip(self.sliders, numbers)):
            if recenter:
                self.ranges[i] = slider_range(number)
            low, high = self.ranges[i]
            steps = int(round(high - low)) if self.integers[i] else FLOAT_STEPS
            slider.blockSignals(True)
            slider.setRange(0, max(steps, 1))
            slider.setValue(int(round((number - low) / (high - low) * steps)) if high > low else 0)
            slider.blockSignals(False)

    def mark(self, changed: bool, error: str = "") -> None:
        """Подсветка: жёлтым — несохранённая правка, красным — ошибка ввода (текст ошибки — в подсказке)."""
        self.edit.setStyleSheet(INVALID_STYLE if error else CHANGED_STYLE if changed else "")
        tip = self.setting.note or self.setting.comment
        self.edit.setToolTip(f"{error}\n\n{tip}" if error else tip)

    def _typed(self, text: str) -> None:
        """Набран текст: строки — без кавычек (кавычки добавляются сами), остальное — как в файле."""
        self.edited.emit(self.kind, self.setting.name, value_text(text) if self.is_text else text)

    def _slid(self, i: int, position: int) -> None:
        """Ползунок i сдвинут: новое число в поле (у кортежа — меняется только i-е число)."""
        try:
            numbers = _numbers(literal(self.edit.text()))
        except ValueError:
            numbers = None
        numbers = numbers if numbers is not None and len(numbers) == len(self.sliders) else _numbers(self.setting.value)
        low, high = self.ranges[i]
        if self.integers[i]:
            numbers[i] = int(round(low)) + position
            texts = [str(n) if isinstance(n, int) else repr(n) for n in numbers]
        else:
            numbers[i] = low + (high - low) * position / FLOAT_STEPS
            step = (high - low) / FLOAT_STEPS
            texts = [number_text(n, step) if k == i else (str(n) if isinstance(n, int) else repr(n))
                     for k, n in enumerate(numbers)]
        if self.is_tuple:
            text = "(" + ", ".join(texts) + ("," if len(texts) == 1 else "") + ")"
        else:
            text = texts[0]
        self.edit.setText(text)
        self.sliding = True
        try:
            self.edited.emit(self.kind, self.setting.name, text)
        finally:
            self.sliding = False


class ParamsPanel(QWidget):
    """Панель «Настройки»: значения settings.py и thresholds.py эксперимента с несохранёнными правками.

    load — открыть файлы эксперимента (модули settings и thresholds); working — значения с правками
    (для расчёта кадров); save — записать правки в файлы. Если настройки не из файлов (например, в тестах),
    панель только сообщает об этом, а working возвращает их как есть.
    """

    edited = Signal()  # значения с правками изменились — пересчитать кадр
    changed = Signal()  # изменились несохранённые правки
    message = Signal(str)  # сообщение для строки состояния

    def __init__(self) -> None:
        super().__init__()
        self.modules: dict[str, Any] = {}
        self.files: dict[str, SettingsFile] = {}
        self.fields: dict[tuple[str, str], ParamField] = {}
        self.headers: list[tuple[QLabel, list[ParamField]]] = []  # заголовки разделов и их поля (для поиска)
        self.where = QLabel()
        self.where.setWordWrap(True)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Найти: имя или слово из комментария")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        self.save_button = QPushButton("Сохранить настройки")
        self.save_button.clicked.connect(lambda: self.save())
        self.revert_button = QPushButton("Вернуть как в файлах")
        self.revert_button.clicked.connect(self.discard)
        for b in (self.save_button, self.revert_button):
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.changes_label = QLabel()
        buttons = QHBoxLayout()
        buttons.addWidget(self.save_button)
        buttons.addWidget(self.revert_button)
        self.form = QVBoxLayout()
        content = QWidget()
        column = QVBoxLayout(content)
        column.addLayout(self.form)
        column.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(content)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self.where)
        layout.addWidget(self.search)
        layout.addLayout(buttons)
        layout.addWidget(self.changes_label)
        layout.addWidget(scroll, 1)

    @property
    def has_changes(self) -> bool:
        """Есть ли несохранённые правки настроек."""
        return any(f.changed() for f in self.files.values())

    def load(self, settings: ModuleType, thresholds: ModuleType) -> None:
        """Открыть файлы эксперимента. Если это те же файлы, несохранённые правки остаются (если ещё подходят)."""
        old = {kind: (f.path, dict(f.edits)) for kind, f in self.files.items()}
        self.modules = {"settings": settings, "thresholds": thresholds}
        self.files = {}
        for kind, module in self.modules.items():
            path = getattr(module, "__file__", None)
            if not path:
                continue
            self.files[kind] = SettingsFile(Path(path))
            if kind in old and old[kind][0] == Path(path):
                for name, text in old[kind][1].items():
                    if name in self.files[kind].settings:
                        try:
                            self.files[kind].set_text(name, text)
                        except ValueError:
                            pass
        self._build()

    def _build(self) -> None:
        """Поля всех значений файлов, по разделам."""
        while self.form.count():
            item = self.form.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self.fields, self.headers = {}, []
        if not self.files:
            self.where.setText("Настройки эксперимента не из файлов — здесь их не поправить.")
        else:
            self.where.setText("Файлы: " + ", ".join(str(f.path) for f in self.files.values()))
        bold = QFont()
        bold.setBold(True)
        for kind, f in self.files.items():
            title = QLabel(KINDS[kind])
            title.setFont(bold)
            title.setWordWrap(True)
            title.setStyleSheet("background: #dde6f3; padding: 3px;")
            self.form.addWidget(title)
            file_fields: list[ParamField] = []
            self.headers.append((title, file_fields))
            section, section_fields = None, []
            for setting in f.settings.values():
                if setting.section != section:
                    section, section_fields = setting.section, []
                    header = QLabel(section)
                    header.setWordWrap(True)
                    header.setStyleSheet("color: #1f5fd0; padding-top: 6px;")
                    self.form.addWidget(header)
                    self.headers.append((header, section_fields))
                field = ParamField(kind, setting)
                field.edited.connect(self._edited)
                field.show_text(f.text(setting.name), f.changed(setting.name))
                self.form.addWidget(field)
                self.fields[(kind, setting.name)] = field
                file_fields.append(field)
                section_fields.append(field)
        self._filter(self.search.text())
        self._update_counts()

    def _filter(self, words: str) -> None:
        """Показать только поля, подходящие под поиск (и заголовки их разделов)."""
        words = words.strip()
        for field in self.fields.values():
            field.setVisible(field.matches(words))
        for header, fields in self.headers:
            header.setVisible(any(f.matches(words) for f in fields))

    def _update_counts(self) -> None:
        """Подпись «несохранённых правок» и доступность кнопок."""
        n = sum(len(f.edits) for f in self.files.values())
        self.changes_label.setText(f"Несохранённых правок: {n}" if n else "Все значения — как в файлах")
        self.save_button.setEnabled(n > 0)
        self.revert_button.setEnabled(n > 0)

    def _edited(self, kind: str, name: str, text: str) -> None:
        """Правка значения в поле или ползунком: проверить, запомнить, пересчитать кадр."""
        field = self.fields[(kind, name)]
        try:
            self.files[kind].set_text(name, text)
        except ValueError as error:
            field.mark(False, str(error))
            return
        field.show_text(self.files[kind].text(name), self.files[kind].changed(name), keep_text=True)
        self._update_counts()
        self.edited.emit()
        self.changed.emit()

    def set_text(self, kind: str, name: str, text: str) -> None:
        """Правка значения name (текст — как в файле), как если бы её набрали в поле."""
        field = self.fields[(kind, name)]
        try:
            field.edit.setText(literal(text) if field.is_text else text)
        except ValueError:
            field.edit.setText(text)
        self._edited(kind, name, text)

    def working(self) -> tuple[Any, Any]:
        """Настройки и пороги с несохранёнными правками — объекты с теми же именами, что модули."""
        result = []
        for kind in ("settings", "thresholds"):
            module = self.modules[kind]
            if kind not in self.files:
                result.append(module)
                continue
            values = {name: getattr(module, name) for name in dir(module) if name.isupper()}
            values.update(self.files[kind].values())
            result.append(SimpleNamespace(**values))
        return result[0], result[1]

    def discard(self) -> None:
        """Забыть правки («Вернуть как в файлах»)."""
        for f in self.files.values():
            f.discard()
        self._build()
        self.edited.emit()
        self.changed.emit()

    def save(self) -> bool:
        """Записать правки в файлы (только изменённые значения). True — записано или нечего записывать."""
        saved = []
        for kind, f in self.files.items():
            try:
                names = f.save()
            except (OSError, ValueError) as error:
                QMessageBox.warning(self, "Настройки не сохранены", f"{f.path.name}: {error}")
                return False
            for name in names:  # модуль в памяти — как файл (без перезапуска)
                setattr(self.modules[kind], name, f.value(name))
            saved += names
        self._build()
        self.changed.emit()
        self.message.emit(f"Настройки сохранены: {', '.join(saved)}" if saved else "Несохранённых настроек нет")
        return True
