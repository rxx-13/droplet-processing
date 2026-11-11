"""Окно полной таблицы записей: все записи и колонки, правка ячеек, новые колонки, растягивание значений.

Открывается с вкладки «Записи» кнопкой «Полная таблица…» (Ctrl+T) — как дополнительный инструмент:
обычная работа с полями одной записи остаётся справа на «Записях». Правки здесь идут в ту же таблицу
(lib/common/table_edit.py): значения, несохранённые правки, отмена и сохранение — общие; файл xlsx меняется
только по Ctrl+S (кнопка «Сохранить» в окне приложения тоже пишет эту таблицу).

Что можно:
* менять любую ячейку двойным щелчком или набором с клавиатуры (запятая — тоже десятичный знак);
* добавлять колонки: кнопка «Добавить колонку…», у новой колонки есть имя и описание (2-я строка таблицы);
* растягивать значения: выделить ячейки и потянуть за синий квадратик в правом нижнем углу выделения
  (вниз или вверх — источником берётся крайняя строка выделения с противоположной стороны), либо Ctrl+D —
  значение верхней строки выделения на все его строки, а если выделена одна ячейка — до конца таблицы;
  Delete очищает выделенные ячейки;
* отменять и повторять правки по шагам (Ctrl+Z, Ctrl+Y): одна правка ячейки, несколько ячеек сразу или
  добавленная колонка — один шаг; «Вернуть как в файлах» забывает все правки сразу (с подтверждением);
* видеть несохранённые правки жёлтым, как в сайдбаре; у изменённой ячейки в подсказке — значение из файла.

Связь с сайдбаром ненавязчивая: щелчок в поле справа подсвечивает его столбец здесь; щелчок по ячейке
здесь подсвечивает поле этой колонки справа и прокручивает сайдбар к нему (значения и правки при этом
не меняются).
"""

from typing import Any

from PySide6.QtCore import QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QDialog, QDialogButtonBox, QFormLayout,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton,
                               QRubberBand, QStyledItemDelegate, QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)

from lib.common.table_edit import format_value

CHANGED_COLOR = QColor("#fff1b8")  # несохранённая правка — как в сайдбаре
HIGHLIGHT_HEADER_COLOR = QColor("#dbe8fa")  # заголовок столбца, выбранного в сайдбаре
HIGHLIGHT_OVERLAY = QColor(47, 111, 208, 26)  # бледная заливка его ячеек
HANDLE_STYLE = "background: #2f6fd0; border: 1px solid #ffffff;"
MIN_COLUMN_WIDTH, MAX_COLUMN_WIDTH = 70, 260  # ширины столбцов после подгонки по содержимому


class ColumnHighlightDelegate(QStyledItemDelegate):
    """Бледная подсветка столбца, выбранного в сайдбаре (column; −1 — никакого)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.column = -1

    def paint(self, painter: Any, option: Any, index: Any) -> None:
        """Обычная ячейка, поверх — полупрозрачная заливка, если это столбец подсветки."""
        super().paint(painter, option, index)
        if index.column() == self.column:
            painter.fillRect(option.rect, HIGHLIGHT_OVERLAY)


class FillHandle(QWidget):
    """Синий квадратик в правом нижнем углу выделения: потянуть — растянуть значения (как в Excel).
    Источник — крайняя строка выделения с противоположной стороны: тянем вниз — верхняя, вверх — нижняя."""

    def __init__(self, grid: "TableGrid") -> None:
        super().__init__(grid.viewport())
        self.grid = grid
        self.dragging = False
        self.setFixedSize(7, 7)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setStyleSheet(HANDLE_STYLE)
        self.setToolTip("Потяните вниз или вверх, чтобы растянуть значения выделения")
        self.hide()

    def mousePressEvent(self, event: Any) -> None:
        """Начало растягивания (если есть выделение)."""
        if event.button() == Qt.MouseButton.LeftButton:
            self.dragging = self.grid.fill_start()

    def mouseMoveEvent(self, event: Any) -> None:
        """Пока тянут — показать, какие строки будут заполнены."""
        if self.dragging:
            self.grid.fill_preview(self.grid.rowAt(self._point(event).y()))

    def mouseReleaseEvent(self, event: Any) -> None:
        """Отпустили — растянуть значения до строки под курсором."""
        if not self.dragging:
            return
        self.dragging = False
        self.grid.fill_finish(self.grid.rowAt(self._point(event).y()))

    def _point(self, event: Any) -> QPoint:
        """Точка события в координатах таблицы (не квадратика)."""
        return self.mapTo(self.grid.viewport(), event.position().toPoint())


class TableGrid(QTableWidget):
    """Таблица ячеек с маркером растягивания в углу выделения (как в Excel) и клавишей Delete."""

    fill_requested = Signal(int, int, int, object)  # исходная строка, первая и последняя строки, столбцы
    delete_requested = Signal()  # Delete — очистить выделенные ячейки

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.handle = FillHandle(self)
        self.band = QRubberBand(QRubberBand.Shape.Rectangle, self.viewport())
        self.band.setStyleSheet("background: rgba(47, 111, 208, 40); border: 1px solid #2f6fd0;")
        self._fill: tuple[int, int, list[int]] | None = None  # (первая строка, последняя, столбцы)
        self.itemSelectionChanged.connect(self.update_handle)
        self.currentCellChanged.connect(lambda *_: self.update_handle())
        self.horizontalScrollBar().valueChanged.connect(lambda _: self.update_handle())
        self.verticalScrollBar().valueChanged.connect(lambda _: self.update_handle())

    def resizeEvent(self, event: Any) -> None:
        """Окно изменило размер — маркер мог оказаться за краем."""
        super().resizeEvent(event)
        self.update_handle()

    def selected_bounds(self) -> tuple[int, int, list[int]] | None:
        """Первая и последняя строки выделения и его столбцы; None — ничего не выделено."""
        indexes = self.selectionModel().selectedIndexes() if self.selectionModel() is not None else []
        if not indexes:
            return None
        rows = [index.row() for index in indexes]
        return min(rows), max(rows), sorted({index.column() for index in indexes})

    def update_handle(self) -> None:
        """Показать маркер у правого нижнего угла выделения (если угол виден и ячейка не правится)."""
        handle = self.handle
        if self.state() == QAbstractItemView.State.EditingState or self.selectionModel() is None or not self.rowCount():
            handle.hide()
            return
        indexes = self.selectionModel().selectedIndexes()
        if not indexes:
            handle.hide()
            return
        bottom = max(index.row() for index in indexes)
        right = max(index.column() for index in indexes)
        rect = self.visualRect(self.model().index(bottom, right))
        point = QPoint(rect.right() + 1, rect.bottom() + 1)
        if not self.viewport().rect().contains(point):
            handle.hide()
            return
        handle.move(point.x() - 3, point.y() - 3)
        handle.show()
        handle.raise_()

    def fill_start(self) -> bool:
        """Запомнить выделение для растягивания; False — нечего тянуть."""
        self._fill = self.selected_bounds()
        return self._fill is not None

    def fill_preview(self, row: int) -> None:
        """Показать рамкой строки, которые будут заполнены при отпускании на строке row."""
        if self._fill is None or row < 0:
            return
        top, bottom, columns = self._fill
        if row > bottom:
            first, last = top, row
        elif row < top:
            first, last = row, bottom
        else:
            self.band.hide()
            return
        x0 = self.columnViewportPosition(columns[0])
        x1 = self.columnViewportPosition(columns[-1]) + self.columnWidth(columns[-1])
        y0 = self.rowViewportPosition(first)
        y1 = self.rowViewportPosition(last) + self.rowHeight(last)
        rect = QRect(QPoint(x0, y0), QPoint(x1 - 1, y1 - 1)) & self.viewport().rect()
        if rect.isValid():
            self.band.setGeometry(rect)
            self.band.show()
        else:
            self.band.hide()

    def fill_finish(self, row: int) -> None:
        """Отпустили маркер: сообщить, какие строки и столбцы заполнить значениями строки-источника."""
        self.band.hide()
        if self._fill is None:
            return
        top, bottom, columns = self._fill
        self._fill = None
        if row > bottom:
            source, first, last = top, top, row
        elif row < top:
            source, first, last = bottom, row, bottom
        else:
            return
        self.fill_requested.emit(source, first, last, columns)

    def keyPressEvent(self, event: Any) -> None:
        """Delete — очистить выделенные ячейки (пока ячейка правится, Delete работает в поле)."""
        if event.key() == Qt.Key.Key_Delete and self.state() != QAbstractItemView.State.EditingState:
            self.delete_requested.emit()
            return
        super().keyPressEvent(event)


class AddColumnDialog(QDialog):
    """Имя и описание новой колонки таблицы (описание — во 2-ю строку xlsx, необязательно)."""

    def __init__(self, headers: list[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.existing = set(headers)
        self.setWindowTitle("Новая колонка")
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("например, ustar")
        self.description_edit = QLineEdit()
        self.description_edit.setPlaceholderText("например, «скорость, м/с» — необязательно")
        self.error = QLabel()
        self.error.setStyleSheet("color: #c00000;")
        form = QFormLayout()
        form.addRow("Имя колонки:", self.name_edit)
        form.addRow("Описание:", self.description_edit)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.error)
        layout.addWidget(buttons)

    def _accept(self) -> None:
        """Проверить имя (не пустое и не занятое) и закрыть с согласием."""
        name = self.values()[0]
        if not name:
            self.error.setText("Имя колонки не может быть пустым")
        elif name in self.existing:
            self.error.setText(f"Колонка {name} уже есть")
        else:
            self.accept()

    def values(self) -> tuple[str, str]:
        """Имя и описание как набраны (без пробелов по краям)."""
        return self.name_edit.text().strip(), self.description_edit.text().strip()


class TableEditor(QDialog):
    """Окно полной таблицы (см. описание модуля). page — RecordsPage: таблица, правки, отмена и
    сохранение — общие с вкладкой «Записи»."""

    def __init__(self, page: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent if parent is not None else page)
        self.page = page
        self.headers: list[str] = []
        self.highlighted: str | None = None
        self._shown_headers: list[str] = []
        self._updating = False  # сейчас таблицу заполняем мы сами — itemChanged не правка
        self._needs_refresh = False
        self.setModal(False)
        self.setSizeGripEnabled(True)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.resize(1250, 720)
        self._build()
        page.cells_changed.connect(self.cells_updated)
        page.table_reloaded.connect(self.refresh)
        page.column_focused.connect(self.highlight_column)
        page.message.connect(self.status.setText)
        self.refresh()

    # ------------------------------------------------------------------ разметка

    def _build(self) -> None:
        """Кнопки, таблица и строка состояния."""
        def button(text: str, slot: Any, tip: str = "") -> QPushButton:
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(lambda checked=False: slot())
            return b

        self.undo_button = button("↶ Отменить (Ctrl+Z)", self.undo, "Отменить последнюю правку (шаг за шагом)")
        self.redo_button = button("↷ Повторить (Ctrl+Y)", self.redo, "Повторить отменённую правку")
        self.add_button = button("Добавить колонку…", self.add_column,
                                 "Новая колонка таблицы: имя и описание (2-я строка xlsx)")
        self.fill_button = button("Растянуть вниз (Ctrl+D)", self.fill_down,
                                  "Выделить ячейку или строки: значение верхней строки выделения — на все его "
                                  "строки;\nодна ячейка — до конца таблицы. Ещё можно потянуть синий квадратик "
                                  "в углу выделения.")
        self.revert_button = button("Вернуть как в файлах", self.revert,
                                    "Забыть все несохранённые правки таблицы (в отличие от Ctrl+Z — сразу все)")
        self.changes_label = QLabel()
        self.status = QLabel()
        self.status.setStyleSheet("color: #707070;")
        self.status.setWordWrap(True)
        row = QHBoxLayout()
        for b in (self.undo_button, self.redo_button, self.add_button, self.fill_button, self.revert_button):
            row.addWidget(b)
        row.addSpacing(12)
        row.addWidget(self.changes_label)
        row.addStretch(1)
        hint = QLabel("Двойной щелчок — правка ячейки. Выделите ячейки и потяните синий квадратик в правом "
                      "нижнем углу выделения (или Ctrl+D), чтобы растянуть значения; Delete — очистить.")
        hint.setStyleSheet("color: #707070;")
        hint.setWordWrap(True)

        self.grid = TableGrid()
        self.grid.setAlternatingRowColors(True)
        self.grid.setWordWrap(False)
        self.grid.setSortingEnabled(False)
        self.grid.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.grid.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.grid.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked
                                  | QAbstractItemView.EditTrigger.EditKeyPressed
                                  | QAbstractItemView.EditTrigger.AnyKeyPressed)
        self.grid.verticalHeader().setDefaultSectionSize(22)
        header = self.grid.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(False)
        self.delegate = ColumnHighlightDelegate(self.grid)
        self.grid.setItemDelegate(self.delegate)
        self.grid.itemChanged.connect(self._item_changed)
        self.grid.currentCellChanged.connect(self._current_cell_changed)
        self.grid.fill_requested.connect(self._fill_requested)
        self.grid.delete_requested.connect(self.clear_selected)

        layout = QVBoxLayout(self)
        layout.addLayout(row)
        layout.addWidget(hint)
        layout.addWidget(self.grid, 1)
        layout.addWidget(self.status)
        for key, slot in ((QKeySequence.StandardKey.Undo, self.undo), (QKeySequence.StandardKey.Redo, self.redo),
                          (QKeySequence("Ctrl+Shift+Z"), self.redo), (QKeySequence("Ctrl+D"), self.fill_down),
                          (QKeySequence.StandardKey.Save, self.save)):
            QShortcut(QKeySequence(key), self).activated.connect(slot)

    # ------------------------------------------------------------------ обновление из таблицы

    def showEvent(self, event: Any) -> None:
        """Окно показано: перестроить, если таблицу меняли, пока оно было закрыто."""
        super().showEvent(event)
        if self._needs_refresh:
            self._rebuild()

    def refresh(self) -> None:
        """Перестроить таблицу из EditableTable (колонки, строки, отметки правок), сохранив прокрутку
        и текущую ячейку. Скрытое окно перестроится при показе."""
        if self.isHidden():
            self._needs_refresh = True
            return
        self._rebuild()

    def _rebuild(self) -> None:
        """Заполнить таблицу заново (refresh, когда окно уже открыто)."""
        table = self.page.table
        if self.grid.state() == QAbstractItemView.State.EditingState:
            QTimer.singleShot(200, self.refresh)  # не выбивать правку ячейки из-под рук
            return
        self._needs_refresh = False
        current = self.grid.currentIndex()
        keep_row = current.row() if current.isValid() else 0
        keep_header = (self.headers[current.column()]
                       if current.isValid() and 0 <= current.column() < len(self.headers) else None)
        hscroll = self.grid.horizontalScrollBar().value()
        vscroll = self.grid.verticalScrollBar().value()
        self._updating = True
        self.grid.blockSignals(True)
        try:
            self.headers = list(table.headers) if table is not None else []
            self.grid.setRowCount(0)
            self.grid.setColumnCount(0)
            self.grid.setColumnCount(len(self.headers))
            self.grid.setRowCount(len(table) if table is not None else 0)
            if self.headers:
                self.grid.setHorizontalHeaderLabels(self.headers)
            for column, header in enumerate(self.headers):
                item = self.grid.horizontalHeaderItem(column)
                if item is not None:
                    description = table.descriptions.get(header, "")
                    item.setToolTip(f"{header}\n{description}" if description else header)
            for i in range(self.grid.rowCount()):
                for column, header in enumerate(self.headers):
                    item = QTableWidgetItem()
                    self._decorate(item, i, header)
                    self.grid.setItem(i, column, item)
            if self.headers != self._shown_headers:
                self._set_widths()
                self._shown_headers = list(self.headers)
            if self.headers and self.grid.rowCount():
                row = min(max(keep_row, 0), self.grid.rowCount() - 1)
                column = self.headers.index(keep_header) if keep_header in self.headers else 0
                self.grid.setCurrentCell(row, column)
            self.grid.horizontalScrollBar().setValue(hscroll)
            self.grid.verticalScrollBar().setValue(vscroll)
        finally:
            self.grid.blockSignals(False)
            self._updating = False
        self._update_buttons()
        self.grid.update_handle()
        self._apply_highlight()  # столбец мог смениться или исчезнуть

    def _set_widths(self) -> None:
        """Ширины столбцов по содержимому (в разумных пределах); ручную правку ширин не трогает."""
        self.grid.resizeColumnsToContents()
        for column in range(self.grid.columnCount()):
            width = self.grid.columnWidth(column)
            self.grid.setColumnWidth(column, min(max(width + 8, MIN_COLUMN_WIDTH), MAX_COLUMN_WIDTH))

    def _decorate(self, item: QTableWidgetItem, i: int, header: str) -> None:
        """Текст, выравнивание, цвет и подсказка ячейки по значению таблицы."""
        table = self.page.table
        value = table.value(i, header)
        item.setText(format_value(value))
        numeric = isinstance(value, (int, float)) and not isinstance(value, bool)
        item.setTextAlignment((Qt.AlignmentFlag.AlignRight if numeric else Qt.AlignmentFlag.AlignLeft)
                              | Qt.AlignmentFlag.AlignVCenter)
        description = table.descriptions.get(header, "")
        if table.changed(i, header):
            item.setBackground(QBrush(CHANGED_COLOR))
            original = f"в файле: {format_value(table.original(i, header)) or 'пусто'}"
            item.setToolTip(f"{original}\n{description}" if description else original)
        else:
            item.setBackground(QBrush())
            item.setToolTip(description)

    def cells_updated(self, cells: list[tuple[int, str]]) -> None:
        """Обновить отдельные ячейки после правки в сайдбаре, отмены или растягивания."""
        table = self.page.table
        if table is None or self.isHidden():
            return
        self._updating = True
        try:
            for i, header in cells:
                if header not in self.headers or not 0 <= i < self.grid.rowCount():
                    self._updating = False
                    self.refresh()  # состав колонок или строк изменился — перестроить
                    return
                column = self.headers.index(header)
                item = self.grid.item(i, column)
                if item is None:
                    item = QTableWidgetItem()
                    self.grid.setItem(i, column, item)
                self._decorate(item, i, header)
        finally:
            self._updating = False
        self._update_buttons()
        self.grid.update_handle()

    # ------------------------------------------------------------------ правки

    def _item_changed(self, item: QTableWidgetItem) -> None:
        """Пользователь закончил правку ячейки: разобрать текст и записать в таблицу (ошибка — вернуть)."""
        table = self.page.table
        row, column = item.row(), item.column()
        if (self._updating or table is None or not 0 <= column < len(self.headers)
                or not 0 <= row < len(table)):
            return
        header = self.headers[column]
        try:
            value = table.parse(header, item.text())
        except ValueError as error:
            self.page.message.emit(str(error))
            self._updating = True
            self._decorate(item, row, header)
            self._updating = False
            return
        self.page.set_value(header, value, [row])

    def _current_cell_changed(self, row: int, column: int, *_: Any) -> None:
        """Щелчок или стрелки по таблице: подсветить поле этой колонки в сайдбаре."""
        if not self._updating and 0 <= column < len(self.headers):
            self.page.highlight_field(self.headers[column])

    def clear_selected(self) -> None:
        """Delete: очистить выделенные ячейки одной правкой."""
        indexes = self.grid.selectionModel().selectedIndexes()
        cells = [(index.row(), self.headers[index.column()], None) for index in indexes
                 if 0 <= index.column() < len(self.headers)]
        if not cells:
            return
        changed = self.page.apply_cells(cells)
        if changed:
            self.page.message.emit(f"Очищено ячеек: {changed}")

    def fill_down(self) -> None:
        """Ctrl+D: значение верхней строки выделения — на все выделенные строки (одна ячейка — до конца)."""
        if self.page.table is None:
            return
        bounds = self.grid.selected_bounds()
        if bounds is None:
            self.page.message.emit("Сначала выделите ячейку или строки")
            return
        top, bottom, columns = bounds
        if top == bottom:
            bottom = len(self.page.table) - 1
            if bottom <= top:
                self.page.message.emit("Растягивать некуда: выделена последняя строка")
                return
        headers = [self.headers[column] for column in columns]
        self.page.fill_down(top, list(range(top, bottom + 1)), headers)

    def _fill_requested(self, source: int, first: int, last: int, columns: object) -> None:
        """Маркер в углу выделения отпущен: растянуть значения строки source."""
        headers = [self.headers[column] for column in columns if 0 <= column < len(self.headers)]
        self.page.fill_down(source, list(range(first, last + 1)), headers)

    def add_column(self) -> None:
        """«Добавить колонку…»: спросить имя и описание, добавить колонку в таблицу."""
        dialog = AddColumnDialog(self.headers, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        name, description = dialog.values()
        if self.page.add_column(name, description):
            self.page.message.emit(f"Колонка {name} добавлена — в таблицу по Ctrl+S")
            self.highlight_column(name)

    def undo(self) -> None:
        """Ctrl+Z: отменить правку таблицы; пока правится ячейка — отменить набор в её поле."""
        focus = QApplication.focusWidget()
        if self.grid.state() == QAbstractItemView.State.EditingState and isinstance(focus, QLineEdit):
            focus.undo()
            return
        self.page.undo()

    def redo(self) -> None:
        """Ctrl+Y: повторить отменённую правку; пока правится ячейка — повторить набор в её поле."""
        focus = QApplication.focusWidget()
        if self.grid.state() == QAbstractItemView.State.EditingState and isinstance(focus, QLineEdit):
            focus.redo()
            return
        self.page.redo()

    def save(self) -> None:
        """Ctrl+S: сохранить таблицу (как «Сохранить в таблицу» на «Записях»); недописанная ячейка
        сначала записывается (фокус уходит из её поля)."""
        if self.grid.state() == QAbstractItemView.State.EditingState:
            self.grid.setFocus()
            QApplication.processEvents()
        self.page.save()

    def revert(self) -> None:
        """«Вернуть как в файлах»: забыть все несохранённые правки таблицы (с подтверждением)."""
        if self.page.table is None or not self.page.has_changes:
            return
        answer = QMessageBox.question(self, "Вернуть как в файлах",
                                      "Забыть все несохранённые правки таблицы? Отменить это будет нельзя.",
                                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if answer == QMessageBox.StandardButton.Yes:
            self.page.discard()

    # ------------------------------------------------------------------ подсветка связи с сайдбаром

    def highlight_column(self, header: str) -> None:
        """Подсветить столбец колонки header (щелчок в её поле в сайдбаре) и прокрутить к нему."""
        self.highlighted = header if header in self.headers else None
        if self.isHidden():
            return
        self._apply_highlight()
        if self.highlighted is not None:
            self._scroll_to_column(self.headers.index(self.highlighted))

    def _apply_highlight(self) -> None:
        """Заголовок и ячейки подсвеченного столбца (column делегата — −1, если колонки нет)."""
        column = self.headers.index(self.highlighted) if self.highlighted in self.headers else -1
        self.delegate.column = column
        self.grid.viewport().update()
        for c in range(self.grid.columnCount()):
            item = self.grid.horizontalHeaderItem(c)
            if item is None:
                continue
            font = item.font()
            font.setBold(c == column)
            item.setFont(font)
            item.setBackground(QBrush(HIGHLIGHT_HEADER_COLOR) if c == column else QBrush())

    def _scroll_to_column(self, column: int) -> None:
        """Прокрутить таблицу по горизонтали, чтобы столбец был виден (строки не двигаются)."""
        x = self.grid.columnViewportPosition(column)
        width = self.grid.columnWidth(column)
        view = self.grid.viewport().width()
        bar = self.grid.horizontalScrollBar()
        if x < 0:
            bar.setValue(bar.value() + x - 8)
        elif x + width > view:
            bar.setValue(bar.value() + x + width - view + 8)

    def _update_buttons(self) -> None:
        """Доступность кнопок, счётчик правок и заголовок окна."""
        table = self.page.table
        self.undo_button.setEnabled(table is not None and bool(table.undo_stack))
        self.redo_button.setEnabled(table is not None and bool(table.redo_stack))
        self.add_button.setEnabled(table is not None)
        n = len(table.changes) + len(table.new_columns) if table is not None else 0
        self.changes_label.setText(f"Несохранённых правок: {n} (в файл — Ctrl+S)" if n else "Всё сохранено")
        self.revert_button.setEnabled(n > 0)
        name = table.path.name if table is not None else ""
        self.setWindowTitle(f"Таблица — {name}" + (" *" if n else ""))

    def closeEvent(self, event: Any) -> None:
        """Закрытие окна: снять подсветку поля в сайдбаре и столбца в таблице."""
        self.highlighted = None
        self.delegate.column = -1
        self.page.highlight_field(None)
        super().closeEvent(event)
