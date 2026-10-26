"""Вкладка «Запуск»: стадии st1 → st2 → st4 для отмеченных записей — в фоновых процессах, с ходом работы.

Слева — записи таблицы с флажками: готова ли запись (как на «Записях»), есть ли результаты st1, st2, st4,
и «Ход» — что сейчас делается с записью (последняя строка хода работы, «st1 ✓», ошибка). Вверху — какие
стадии запускать (идут по порядку: st2 берёт результат st1, st4 — результат st2) и сколько записей считать
одновременно (по процессу на запись). «Запустить» — обработка в фоне, окно не подвисает; «Остановить» —
остановить всё сразу (прерванная стадия может оставить неполные файлы — её стоит запустить заново).
Ошибка в одной записи останавливает только её цепочку. Внизу — общий лог (то, что скрипты пишут в консоль).

Считается то же и так же, что скриптами main/ (те же функции process_record, lib/common/jobs.py), с
настройками и таблицей из файлов: если есть несохранённые правки, окно предложит сохранить их перед запуском.
Записи без диапазона кадров пропускаются.
"""

import os
from collections.abc import Callable

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QHBoxLayout, QHeaderView, QLabel, QMessageBox,
                               QPlainTextEdit, QPushButton, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from app.records_page import RESULT_FILES, RecordsPage
from lib.common.jobs import STAGES, Job, JobRunner

POLL_MS = 200  # как часто забирать сообщения процессов
LOG_LINES = 20000  # столько последних строк держит общий лог
COLUMNS = ["Запись", "Готова", *RESULT_FILES, "Ход"]


class JobsPage(QWidget):
    """Вкладка «Запуск». records — вкладка «Записи» (таблица, настройки, отметки результатов);
    save — сохранить правки таблицы и настроек (True — сохранено); workers — сколько записей считать
    одновременно по умолчанию (все ядра, кроме одного)."""

    finished = Signal()  # обработка закончилась — обновить отметки результатов
    message = Signal(str)  # сообщение для строки состояния

    def __init__(self, records: RecordsPage, save: Callable[[], bool], workers: int | None = None) -> None:
        super().__init__()
        self.records, self.save = records, save
        self.runner: JobRunner | None = None
        self.next_id = 1
        self.rows: dict[int, int] = {}  # номер задания -> строка таблицы
        self.counts = {"done": 0, "error": 0, "stopped": 0, "total": 0}

        self.stage_boxes = {stage: QCheckBox(stage) for stage in STAGES}
        for box in self.stage_boxes.values():
            box.setChecked(True)
        self.stage_boxes["st1"].setToolTip("капли и PIV на каждом кадре (долго: ~1–2 с на кадр)")
        self.stage_boxes["st2"].setToolTip("траектории по результату st1")
        self.stage_boxes["st4"].setToolTip("средние по областям по результату st2")
        self.workers = QSpinBox()
        self.workers.setRange(1, max(1, os.cpu_count() or 1))
        self.workers.setValue(workers or max(1, (os.cpu_count() or 2) - 1))
        self.workers.setToolTip("сколько записей считать одновременно (по процессу на запись)")
        self.start_button = QPushButton("Запустить")
        self.start_button.clicked.connect(self.start)
        self.stop_button = QPushButton("Остановить")
        self.stop_button.clicked.connect(self.stop)
        self.stop_button.setEnabled(False)
        self.progress = QLabel()
        top = QHBoxLayout()
        top.addWidget(QLabel("Стадии:"))
        for box in self.stage_boxes.values():
            top.addWidget(box)
        top.addSpacing(20)
        top.addWidget(QLabel("Записей одновременно:"))
        top.addWidget(self.workers)
        top.addSpacing(20)
        top.addWidget(self.start_button)
        top.addWidget(self.stop_button)
        top.addWidget(self.progress, 1)

        self.list = QTableWidget(0, len(COLUMNS))
        self.list.setHorizontalHeaderLabels(COLUMNS)
        self.list.verticalHeader().setVisible(False)
        self.list.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        header = self.list.horizontalHeader()
        for column in range(len(COLUMNS) - 1):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(len(COLUMNS) - 1, QHeaderView.ResizeMode.Stretch)
        mark_ready = QPushButton("Отметить готовые")
        mark_ready.setToolTip("отметить записи, у которых заданы диапазон кадров, rim и центр")
        mark_ready.clicked.connect(lambda: self._mark(lambda i: self._readiness(i) == "✓"))
        mark_none = QPushButton("Снять отметки")
        mark_none.clicked.connect(lambda: self._mark(lambda i: False))
        marks = QHBoxLayout()
        marks.addWidget(mark_ready)
        marks.addWidget(mark_none)
        marks.addStretch(1)
        upper = QWidget()
        column = QVBoxLayout(upper)
        column.setContentsMargins(0, 0, 0, 0)
        column.addWidget(self.list, 1)
        column.addLayout(marks)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(LOG_LINES)
        self.log.setPlaceholderText("Здесь будет ход обработки")
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(upper)
        splitter.addWidget(self.log)
        splitter.setSizes([500, 300])
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(splitter, 1)

        self.timer = QTimer(self)
        self.timer.setInterval(POLL_MS)
        self.timer.timeout.connect(self._poll)
        self.refresh()

    # ------------------------------------------------------------------ список записей

    def _readiness(self, i: int) -> str:
        """Готова ли запись i (как на вкладке «Записи»)."""
        return self.records.readiness(self.records.table.record(i))

    def refresh(self) -> None:
        """Список записей — по таблице и отметкам результатов вкладки «Записи» (флажки и «Ход» остаются,
        если число записей не изменилось)."""
        table = self.records.table
        n = len(table) if table is not None else 0
        keep = self.list.rowCount() == n
        self.list.setRowCount(n)
        for i in range(n):
            ready = self._readiness(i)
            present = self.records.results_present.get(i, {})
            texts = [table.name(i), ready, *("✓" if present.get(stage) else "" for stage in RESULT_FILES)]
            for column, text in enumerate(texts):
                item = self.list.item(i, column)
                if item is None:
                    item = QTableWidgetItem()
                    self.list.setItem(i, column, item)
                item.setText(text)
                if column > 0:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            name = self.list.item(i, 0)
            name.setFlags(name.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            if not keep:
                name.setCheckState(Qt.CheckState.Unchecked)
            self.list.item(i, 1).setForeground(QBrush(QColor("#1a8f1a" if ready == "✓" else "#808080")))
            if self.list.item(i, len(COLUMNS) - 1) is None or not keep:
                self.list.setItem(i, len(COLUMNS) - 1, QTableWidgetItem(""))

    def _mark(self, condition: Callable[[int], bool]) -> None:
        """Поставить флажки записям, для которых condition(i) истинно, остальным — снять."""
        for i in range(self.list.rowCount()):
            self.list.item(i, 0).setCheckState(Qt.CheckState.Checked if condition(i) else Qt.CheckState.Unchecked)

    def checked(self) -> list[int]:
        """Отмеченные записи (строки таблицы, с 0)."""
        return [i for i in range(self.list.rowCount()) if self.list.item(i, 0).checkState() == Qt.CheckState.Checked]

    def set_checked(self, rows: list[int]) -> None:
        """Отметить записи rows, остальные — снять."""
        self._mark(lambda i: i in rows)

    def _status(self, i: int, text: str, color: str = "#000000") -> None:
        """Колонка «Ход» записи i."""
        item = self.list.item(i, len(COLUMNS) - 1)
        item.setText(text)
        item.setForeground(QBrush(QColor(color)))

    # ------------------------------------------------------------------ запуск

    @property
    def running(self) -> bool:
        """Идёт ли обработка."""
        return self.runner is not None and self.runner.busy()

    def start(self) -> None:
        """«Запустить»: отмеченные записи, выбранные стадии — в фоновые процессы."""
        if self.running:
            return
        stages = tuple(stage for stage, box in self.stage_boxes.items() if box.isChecked())
        rows = self.checked()
        if not stages or not rows:
            self.message.emit("Отметьте записи и стадии")
            return
        if self.records.has_changes or self.records.params.has_changes:
            answer = QMessageBox.question(
                self, "Несохранённые правки", "Обработка идёт с таблицей и настройками из файлов. "
                "Сохранить правки и запустить?", QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel)
            if answer != QMessageBox.StandardButton.Save or not self.save():
                return
        files = self.records.params.files
        if "settings" not in files or "thresholds" not in files:
            self.message.emit("Настройки эксперимента не из файлов — запустить нельзя")
            return
        jobs = []
        for i in rows:
            record = self.records.table.record(i)
            if record is None or not record.has_frames:
                self._status(i, "пропущена: нет диапазона кадров", "#808080")
                continue
            jobs.append(Job(self.next_id, record, stages, str(files["settings"].path), str(files["thresholds"].path)))
            self.rows[self.next_id] = i
            self.next_id += 1
            self._status(i, "ждёт очереди", "#808080")
        if not jobs:
            self.message.emit("Нечего запускать: у отмеченных записей нет диапазона кадров")
            return
        if self.runner is None or self.runner.workers != self.workers.value():
            self.runner = JobRunner(self.workers.value())
        self.counts = {"done": 0, "error": 0, "stopped": 0, "total": len(jobs)}
        self.runner.submit(jobs)
        self.log.appendPlainText(f"=== Запуск: {' → '.join(stages)}, записей {len(jobs)} ===")
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.timer.start()
        self._poll()

    def stop(self) -> None:
        """«Остановить»: всё сразу (ожидающие записи не запустятся, идущие прерываются)."""
        if self.runner is not None:
            self._handle(self.runner.stop())
            self.log.appendPlainText("=== Остановлено ===")
        self._finish()

    def _poll(self) -> None:
        """Таймер: сообщения процессов -> «Ход», лог, счётчики; всё закончилось — итог."""
        if self.runner is None:
            return
        self._handle(self.runner.poll())
        if not self.runner.busy():
            self._finish()

    def _handle(self, messages: list[tuple[int, str, str, str]]) -> None:
        """Разобрать сообщения процессов."""
        for job_id, kind, stage, text in messages:
            i = self.rows[job_id]
            name = self.records.table.name(i) if i < len(self.records.table) else str(job_id)
            if kind == "log":
                self.log.appendPlainText(f"{stage} {text}")
                self._status(i, f"{stage}: {text.split(': ', 1)[-1]}")
            elif kind == "start":
                self._status(i, f"{stage}: начато")
            elif kind == "done":
                self._status(i, f"{stage} ✓", "#1a8f1a")
                if stage == self.runner.jobs[job_id].stages[-1]:
                    self.counts["done"] += 1
            elif kind == "error":
                self._status(i, f"ошибка{' в ' + stage if stage else ''}: {text}", "#c00000")
                self.log.appendPlainText(f"!!! {name}: ошибка{' в ' + stage if stage else ''}: {text}")
                self.counts["error"] += 1
            elif kind == "stopped":
                self._status(i, text, "#c06000")
                self.counts["stopped"] += 1
        c = self.counts
        rest = c["total"] - c["done"] - c["error"] - c["stopped"]
        self.progress.setText(f"готово {c['done']} из {c['total']}" + (f", с ошибкой {c['error']}" if c["error"] else "")
                              + (f", остановлено {c['stopped']}" if c["stopped"] else "")
                              + (f", в работе и в очереди {rest}" if rest else ""))

    def _finish(self) -> None:
        """Обработка закончилась: кнопки, отметки результатов, сообщение."""
        self.timer.stop()
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.records.refresh_results()
        self.refresh()
        self.finished.emit()
        self.message.emit(f"Обработка закончена: {self.progress.text()}")

    def shutdown(self) -> None:
        """Окно закрывается: остановить обработку."""
        self.timer.stop()
        if self.runner is not None and self.runner.busy():
            self.runner.stop()
