"""Главное окно приложения: выбор эксперимента, вкладки, строка состояния.

Эксперимент при запуске — из experiments/current.py. Другой можно выбрать в списке вверху окна: это меняет
эксперимент только в окне; «Сделать текущим» записывает выбранный в experiments/current.py (тогда его же
обрабатывают скрипты main/ и окно просмотра st1). Вкладки: «Записи» — app/records_page.py (справа на ней —
поля таблицы и панель «Настройки», app/params_page.py); «Запуск» — app/jobs_page.py; «Результаты» —
app/results_page.py. Пока идёт обработка, эксперимент не сменить; при закрытии окно предложит её остановить.
Ctrl+S — сохранить правки таблицы и настроек; F5 — перечитать settings.py, thresholds.py и таблицу
(несохранённые правки остаются, таблица с правками не перечитывается). При закрытии окна или смене
эксперимента с несохранёнными правками окно спросит, сохранить ли их.
"""

import importlib
from pathlib import Path
from types import ModuleType

from PySide6.QtGui import QAction, QCloseEvent, QKeySequence
from PySide6.QtWidgets import QComboBox, QLabel, QMainWindow, QMessageBox, QTabWidget

from app.jobs_page import JobsPage
from app.records_page import RecordsPage
from app.results_page import ResultsPage
from lib.common.config_edit import current_experiment, set_current_experiment

ROOT = Path(__file__).resolve().parents[1]
CURRENT_FILE = ROOT / "experiments" / "current.py"  # какой эксперимент обрабатывают скрипты main/
STATUS_MS = 20000  # сколько держится сообщение в строке состояния


def experiment_names() -> list[str]:
    """Эксперименты — папки experiments/, в которых есть settings.py и thresholds.py."""
    folder = ROOT / "experiments"
    if not folder.is_dir():
        return []
    return sorted(p.name for p in folder.iterdir() if (p / "settings.py").is_file() and (p / "thresholds.py").is_file())


def experiment_name(settings: ModuleType) -> str:
    """Имя эксперимента по модулю experiments.<эксперимент>.settings ('' — модуль не из experiments/)."""
    parts = getattr(settings, "__name__", "").split(".")
    return parts[1] if len(parts) >= 3 and parts[0] == "experiments" else ""


class MainWindow(QMainWindow):
    """Главное окно. settings, thresholds — модули эксперимента (по умолчанию — из experiments/current.py);
    workers — число фоновых процессов расчёта кадров (по умолчанию все ядра, кроме одного; 0 — без них)."""

    def __init__(self, settings: ModuleType | None = None, thresholds: ModuleType | None = None,
                 workers: int | None = None) -> None:
        super().__init__()
        if settings is None or thresholds is None:
            from experiments.current import settings, thresholds
        self.settings, self.thresholds = settings, thresholds
        self.resize(1700, 960)

        toolbar = self.addToolBar("Главная")
        toolbar.setMovable(False)
        toolbar.addWidget(QLabel("  Эксперимент: "))
        self.experiment = QComboBox()
        name = experiment_name(settings)
        names = experiment_names()
        if name and name not in names:
            names.append(name)
        self.experiment.addItems(names if name else ["—"])
        self.experiment.setCurrentText(name or "—")
        self.experiment.setEnabled(bool(name))
        self.experiment.setToolTip("Эксперимент — папка в experiments/ (experiments/current.py при выборе не меняется)")
        self.experiment.currentTextChanged.connect(self._choose_experiment)
        toolbar.addWidget(self.experiment)
        self.make_current = QAction("Сделать текущим", self)
        self.make_current.triggered.connect(self._make_current)
        toolbar.addAction(self.make_current)
        toolbar.addSeparator()
        for text, key, slot, tip in (
                ("Сохранить (Ctrl+S)", QKeySequence.StandardKey.Save, self.save, "записать правки таблицы и настроек"),
                ("Перечитать (F5)", QKeySequence("F5"), self.reload, "перечитать settings.py, thresholds.py и таблицу")):
            action = QAction(text, self)
            action.setShortcut(key)
            action.setToolTip(tip)
            action.triggered.connect(slot)
            toolbar.addAction(action)

        self.records = RecordsPage(settings, thresholds, workers)
        self.records.changed.connect(self._update_title)
        self.records.message.connect(lambda text: self.statusBar().showMessage(text, STATUS_MS))
        self.jobs = JobsPage(self.records, self.save)
        self.jobs.message.connect(lambda text: self.statusBar().showMessage(text, STATUS_MS))
        self.results = ResultsPage(self.records)
        self.tabs = QTabWidget()
        self.tabs.addTab(self.records, "Записи")
        self.tabs.addTab(self.jobs, "Запуск")
        self.tabs.addTab(self.results, "Результаты")
        self.tabs.currentChanged.connect(self._tab_changed)
        self.setCentralWidget(self.tabs)
        self._update_title()

    @property
    def has_changes(self) -> bool:
        """Есть ли несохранённые правки таблицы или настроек."""
        return self.records.has_changes or self.records.params.has_changes

    def _update_title(self) -> None:
        """Заголовок окна: эксперимент; «*» — есть несохранённые правки; доступность «Сделать текущим»."""
        name = experiment_name(self.settings)
        self.setWindowTitle(f"Обработка съёмки капель — {name or 'эксперимент'}" + (" *" if self.has_changes else ""))
        current = current_experiment(CURRENT_FILE)
        self.make_current.setEnabled(bool(name) and name != current)
        self.make_current.setToolTip(f"Записать «{name}» в experiments/current.py — его будут обрабатывать скрипты "
                                     f"main/ (сейчас там «{current or '—'}»)" if name != current
                                     else "Этот эксперимент уже текущий (experiments/current.py)")

    def _tab_changed(self, index: int) -> None:
        """Открыта вкладка: «Запуск» и «Результаты» берут свежие записи и отметки результатов."""
        page = self.tabs.widget(index)
        if page is self.jobs:
            self.jobs.refresh()
        elif page is self.results:
            self.results.refresh()

    def save(self) -> bool:
        """Сохранить правки таблицы и настроек (Ctrl+S). True — всё записано."""
        if not self.has_changes:
            self.statusBar().showMessage("Несохранённых правок нет", STATUS_MS)
            return True
        table = self.records.save() if self.records.has_changes else True
        params = self.records.params.save() if self.records.params.has_changes else True
        self._update_title()
        return table and params

    def _confirm_discard(self) -> bool:
        """Есть несохранённые правки — спросить: сохранить, не сохранять или отменить. True — можно продолжать."""
        if not self.has_changes:
            return True
        where = [what for what, changed in (("таблице", self.records.has_changes),
                                            ("настройках", self.records.params.has_changes)) if changed]
        answer = QMessageBox.question(
            self, "Несохранённые правки", f"В {' и '.join(where)} есть несохранённые правки. Сохранить их?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel)
        if answer == QMessageBox.StandardButton.Save:
            return self.save()
        if answer == QMessageBox.StandardButton.Discard:
            self.records.discard()
            self.records.params.discard()
            return True
        return False

    def _choose_experiment(self, name: str) -> None:
        """Выбран другой эксперимент в списке: прочитать его settings.py и thresholds.py, открыть таблицу."""
        old = experiment_name(self.settings)
        if not name or name == old:
            return
        modules = None
        if self.jobs.running:
            self.statusBar().showMessage("Идёт обработка записей — сначала остановите её (вкладка «Запуск»)", STATUS_MS)
        elif self._confirm_discard():
            try:
                modules = [importlib.reload(importlib.import_module(f"experiments.{name}.{part}"))
                           for part in ("settings", "thresholds")]
            except Exception as error:  # ошибка в файле настроек — остаться на прежнем эксперименте
                QMessageBox.critical(self, "Ошибка в настройках", f"experiments/{name}: {type(error).__name__}: {error}")
        if modules is None:
            self.experiment.blockSignals(True)
            self.experiment.setCurrentText(old)
            self.experiment.blockSignals(False)
            return
        self.settings, self.thresholds = modules
        self.records.load(self.settings, self.thresholds)
        self.jobs.refresh()
        self._update_title()
        self.statusBar().showMessage(f"Эксперимент {name}", STATUS_MS)

    def _make_current(self) -> None:
        """«Сделать текущим»: записать выбранный эксперимент в experiments/current.py."""
        name = experiment_name(self.settings)
        try:
            set_current_experiment(CURRENT_FILE, name)
        except OSError as error:
            QMessageBox.warning(self, "Не удалось записать experiments/current.py", str(error))
            return
        self._update_title()
        self.statusBar().showMessage(f"Текущий эксперимент (experiments/current.py) — {name}", STATUS_MS)

    def reload(self) -> None:
        """Перечитать settings.py и thresholds.py эксперимента и таблицу (F5), пересчитать кадр."""
        try:
            if isinstance(self.settings, ModuleType):
                self.settings = importlib.reload(self.settings)
            if isinstance(self.thresholds, ModuleType):
                self.thresholds = importlib.reload(self.thresholds)
        except Exception as error:  # ошибка в правленом файле не должна закрывать окно
            QMessageBox.critical(self, "Ошибка в настройках", f"{type(error).__name__}: {error}")
            return
        self.records.load(self.settings, self.thresholds)
        self.jobs.refresh()
        kept = "; таблица не перечитана — в ней есть несохранённые правки" if self.records.has_changes else ""
        self.statusBar().showMessage("Перечитано" + kept, STATUS_MS)
        self._update_title()

    def closeEvent(self, event: QCloseEvent) -> None:
        """Закрытие окна: спросить про идущую обработку и несохранённые правки, остановить фоновые процессы."""
        if self.jobs.running and QMessageBox.question(
                self, "Идёт обработка", "Идёт обработка записей. Остановить её и закрыть окно?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            event.ignore()
            return
        if not self._confirm_discard():
            event.ignore()
            return
        self.jobs.shutdown()
        self.records.shutdown()
        event.accept()
