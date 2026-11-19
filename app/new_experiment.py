"""Окно «Новый эксперимент»: имя, пути через проводник, образец настроек и таблица записей.

Обязательные поля — имя эксперимента (папка experiments/<имя>), папка кадров (IMAGES_DIR),
таблица записей (TABLE_FILE) и папка результатов (OUTPUT_DIR). Таблицы может ещё не быть: тогда
при создании она будет сделана (lib/common/experiment_edit.create_experiment) — колонки с описаниями
и строка на каждую подпапку папки кадров, где есть картинки. settings.py и thresholds.py копируются
из выбранного эксперимента-образца; там заменяются пути, очищаются CONDITIONS и RECORDS.

Пути из полей могут быть полными или от папки проекта; в settings.py записываются полные.
"""

from collections.abc import Callable
from pathlib import Path

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                               QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget)

from lib.common.experiment_edit import check_name, create_experiment
from lib.common.records import project_path


class NewExperimentDialog(QDialog):
    """Окно создания эксперимента. experiments_dir — папка experiments/ проекта; sources — имена
    существующих экспериментов (образцов); source — какой из них выбран сначала.

    created — имя созданного эксперимента ('' — ещё не создан); source_name — из какого эксперимента
    взяты настройки; make_current — флажок «Сделать текущим»; table_created — была ли таблица создана.
    """

    def __init__(self, experiments_dir: Path, sources: list[str], source: str = "",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.experiments_dir = Path(experiments_dir)
        self.created = ""
        self.source_name = ""
        self.table_created = False
        self._auto_table = True  # поля путей следуют за именем, пока их не правили вручную
        self._auto_output = True
        self.setWindowTitle("Новый эксперимент")
        self.resize(760, 0)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("например, clear_water_6")
        self.images_edit = QLineEdit()
        self.images_edit.setPlaceholderText("папка, в подпапках которой лежат кадры записей")
        self.table_edit = QLineEdit()
        self.output_edit = QLineEdit()
        self.table_hint = QLabel()
        self.table_hint.setStyleSheet("color: #666666;")
        self.source_combo = QComboBox()
        self.source_combo.addItems(sources)
        if source in sources:
            self.source_combo.setCurrentText(source)
        self.make_current = QCheckBox("Сделать текущим (experiments/current.py)")
        self.make_current.setChecked(True)
        self.error = QLabel()
        self.error.setStyleSheet("color: #c00000;")
        self.error.setWordWrap(True)
        note = QLabel("settings.py и thresholds.py будут скопированы из выбранного эксперимента; "
                      "IMAGES_DIR, TABLE_FILE и OUTPUT_DIR заменятся, CONDITIONS и RECORDS очистятся. "
                      "Если таблицы нет, она будет создана: колонки с описаниями и строка на каждую "
                      "подпапку папки кадров, где есть картинки.")
        note.setWordWrap(True)
        note.setStyleSheet("color: #666666;")

        form = QFormLayout()
        form.addRow("Имя эксперимента:", self.name_edit)
        form.addRow("Папка с кадрами:", self._path_row(self.images_edit, self.choose_images))
        form.addRow("Таблица записей:", self._path_row(self.table_edit, self.choose_table, "Выбрать или создать…"))
        form.addRow("", self.table_hint)
        form.addRow("Папка результатов:", self._path_row(self.output_edit, self.choose_output))
        form.addRow("Настройки и пороги из:", self.source_combo)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Создать")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self.create)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.make_current)
        layout.addWidget(note)
        layout.addWidget(self.error)
        layout.addWidget(buttons)

        self.name_edit.textChanged.connect(self._name_changed)
        self.table_edit.textChanged.connect(self._table_hint)
        self.table_edit.textEdited.connect(self._table_edited)
        self.output_edit.textEdited.connect(self._output_edited)

    def _path_row(self, edit: QLineEdit, slot: Callable[[], None], text: str = "Выбрать…") -> QWidget:
        """Поле пути и кнопка выбора (text) в одной строке."""
        widget = QWidget()
        row = QHBoxLayout(widget)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(edit, 1)
        button = QPushButton(text)
        button.clicked.connect(lambda checked=False: slot())
        row.addWidget(button)
        return widget

    def _name_changed(self) -> None:
        """Имя набрано: подставить пути по умолчанию, если их ещё не правили вручную."""
        name = self.name_edit.text().strip()
        if self._auto_table:
            self.table_edit.setText(str(self.experiments_dir / name / "records.xlsx") if name else "")
        if self._auto_output:
            self.output_edit.setText(str(self.experiments_dir / name / "results") if name else "")

    def _table_edited(self) -> None:
        """Таблицу правили вручную — больше не заменять её при наборе имени."""
        self._auto_table = False

    def _output_edited(self) -> None:
        """Папку результатов правили вручную — больше не заменять её при наборе имени."""
        self._auto_output = False

    def _table_hint(self) -> None:
        """Подсказка под таблицей: файла нет — он будет создан."""
        path = self._path(self.table_edit)
        self.table_hint.setText("Файла нет — таблица будет создана" if path is not None and not path.exists() else "")

    def _path(self, edit: QLineEdit) -> Path | None:
        """Путь из поля (полный или от папки проекта); None — поле пустое."""
        text = edit.text().strip()
        if not text:
            return None
        path = Path(text)
        return path if path.is_absolute() else project_path(path)

    def _start_dir(self, edit: QLineEdit) -> str:
        """Где открыть проводник: папка из поля (или её родитель, если её ещё нет) или папка эксперимента."""
        text = edit.text().strip()
        if not text:
            return str(self.experiments_dir)
        path = Path(text)
        return str(path if path.is_dir() else path.parent)

    def choose_images(self) -> None:
        """Выбрать папку кадров через проводник."""
        path = QFileDialog.getExistingDirectory(self, "Папка с кадрами", self._start_dir(self.images_edit))
        if path:
            self.images_edit.setText(path)

    def choose_output(self) -> None:
        """Выбрать папку результатов через проводник."""
        path = QFileDialog.getExistingDirectory(self, "Папка результатов", self._start_dir(self.output_edit))
        if path:
            self.output_edit.setText(path)
            self._auto_output = False  # выбранную папку при наборе имени не заменять

    def choose_table(self) -> None:
        """Выбрать существующую таблицу или назвать новую (файл может ещё не существовать)."""
        text = self.table_edit.text().strip()
        directory = Path(text).parent if text else self.experiments_dir
        dialog = QFileDialog(self, "Таблица записей: выбрать файл или назвать новый", str(directory),
                             "Таблица Excel (*.xlsx)")
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
        dialog.setOption(QFileDialog.Option.DontConfirmOverwrite, True)  # выбор существующей — без вопроса
        if text:
            dialog.selectFile(Path(text).name)
        if dialog.exec():
            selected = dialog.selectedFiles()
            if selected:
                self.table_edit.setText(selected[0])
                self._auto_table = False  # выбранную таблицу при наборе имени не заменять

    def create(self) -> bool:
        """Проверить поля и создать эксперимент. False — не создан, причина — в красной строке окна."""
        name = self.name_edit.text().strip()
        images = self._path(self.images_edit)
        table = self._path(self.table_edit)
        output = self._path(self.output_edit)
        source = self.source_combo.currentText()
        error = check_name(name)
        if not error and (self.experiments_dir / name).exists():
            error = f"Папка experiments/{name} уже есть — выберите другое имя"
        if not error and not source:
            error = "Нет эксперимента-образца: в experiments/ нет ни одного эксперимента"
        if not error and (images is None or not images.is_dir()):
            error = "Выберите существующую папку с кадрами (IMAGES_DIR)"
        if not error and table is None:
            error = "Укажите таблицу записей (TABLE_FILE)"
        if not error and table.suffix.lower() != ".xlsx":
            error = "Таблица записей должна быть файлом .xlsx"
        if not error and output is None:
            error = "Укажите папку результатов (OUTPUT_DIR)"
        if error:
            self.error.setText(error)
            return False
        existed = table.exists()
        try:
            create_experiment(self.experiments_dir, name, images, table, output, source)
        except Exception as error:  # показать причину в окне, не закрывая его
            self.error.setText(f"{type(error).__name__}: {error}")
            return False
        self.created, self.source_name, self.table_created = name, source, not existed
        self.error.clear()
        self.accept()
        return True
