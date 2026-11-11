"""Таблица записей (xlsx) для приложения: все колонки, несохранённые правки, отмена, автозаполнение, запись.

Чтение — по тем же правилам, что read_records (lib/common/records.py): первый лист, строка 1 — заголовки,
строка 2 — описания колонок, данные — с 3-й строки. Но здесь берутся все колонки (их набор — из заголовков,
ничего не зашито) и все строки, где есть rec_name.

Правки копятся в памяти (EditableTable.set) и попадают в файл только по EditableTable.save: книга
открывается заново, меняются только изменённые ячейки, остальное (другие ячейки, форматирование, условное
форматирование, ширина колонок) остаётся как было. Колонку, которой в таблице нет (например, bag_length),
можно добавить (EditableTable.add_column): при записи её заголовок и описание встают в первую свободную
колонку. Перед записью проверяется, что в строке та же запись
(rec_name) — на случай, если таблицу тем временем правили в Excel. Если файл открыт в Excel, он не
трогается, а возникает TableSaveError с понятным текстом.

Отмена — по шагам (undo/redo), а не вся таблица сразу: шаг — это одна правка ячейки, правка нескольких
ячеек вместе (batch; «как у предыдущих», растягивание значения) или добавленная колонка. Пока набирают
одно значение в поле, его правки склеиваются в один шаг (merge); break_merge заканчивает такую серию.
После save и discard история очищается: отменять сохранённое нельзя (это уже в файле).

Автозаполнение: подсказка для пустого поля — значение той же колонки у предыдущей заполненной записи
(заполненная — с диапазоном кадров); «как у предыдущих» — колонки, одинаковые во всех заполненных записях.
"""

import io
import shutil
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from openpyxl import load_workbook

from .records import COLUMN_NAMES, REQUIRED_COLUMNS, Record, _integer, _number, _text, project_path


class TableSaveError(Exception):
    """Таблицу не удалось записать (открыта в Excel, строки переставлены и т. п.); файл не изменён."""


@dataclass
class TableRow:
    """Строка данных таблицы: excel_row — номер строки в Excel (с 1); values — значения по заголовкам."""

    excel_row: int
    values: dict[str, Any]


_NONE = object()  # «правки в changes не было» (для истории отмены; None — допустимое значение ячейки)


@dataclass
class _Edit:
    """Одна ячейка в шаге отмены: before/after — её значение в changes до и после правки (или _NONE)."""

    i: int
    header: str
    before: Any
    after: Any


@dataclass
class _Step:
    """Шаг истории отмены: правки ячеек и добавленные в нём колонки; merge — ключ серии правок."""

    merge: str | None
    edits: list[_Edit]
    columns: list[tuple[str, str]]  # добавленные колонки: заголовок, описание


def _same_state(before: Any, after: Any) -> bool:
    """Одинаково ли состояние ячейки в changes до и после правки (учитывая, что правки могло не быть)."""
    if before is _NONE or after is _NONE:
        return before is after
    return same_value(before, after)


def is_empty(value: Any) -> bool:
    """Пустая ли ячейка: None или пустой текст."""
    return value is None or (isinstance(value, str) and not value.strip())


def same_value(a: Any, b: Any) -> bool:
    """Одинаковы ли значения ячеек: пустые равны пустым, числа сравниваются как числа (200 = 200.0)."""
    if is_empty(a) or is_empty(b):
        return is_empty(a) and is_empty(b)
    numbers = (int, float)
    if isinstance(a, numbers) and isinstance(b, numbers) and not isinstance(a, bool) and not isinstance(b, bool):
        return float(a) == float(b)
    return type(a) is type(b) and a == b


def format_value(value: Any) -> str:
    """Значение ячейки как текст для поля ввода (целые числа — без «.0»)."""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def read_table(path: Path) -> tuple[list[str], dict[str, str], list[TableRow]]:
    """Заголовки (непустые, по порядку), описания колонок (строка 2) и строки данных, где есть rec_name."""
    workbook = load_workbook(path, data_only=True)
    rows = [list(row) for row in workbook.worksheets[0].iter_rows(values_only=True)]
    workbook.close()
    columns: list[tuple[int, str]] = []  # (номер колонки с 0, заголовок)
    for i, header in enumerate(_text(h) for h in (rows[0] if rows else [])):
        if header and header not in {h for _, h in columns}:
            columns.append((i, header))
    headers = [h for _, h in columns]
    name = next((h for h in COLUMN_NAMES["rec_name"] if h in headers), None)
    if name is None:
        raise KeyError(f"В таблице {path.name} нет колонки rec_name")

    def cell(row: list[Any], i: int) -> Any:
        return row[i] if i < len(row) else None

    description_row = rows[1] if len(rows) > 1 else []
    descriptions = {h: _text(cell(description_row, i)) for i, h in columns}
    data = []
    for excel_row, row in enumerate(rows[2:], start=3):
        values = {h: cell(row, i) for i, h in columns}
        if _text(values[name]):
            data.append(TableRow(excel_row, values))
    return headers, descriptions, data


def save_cells(path: Path, cells: dict[tuple[int, str], Any], names: dict[int, str], name_header: str,
               new_columns: dict[str, str] | None = None) -> None:
    """Записать ячейки {(строка Excel, заголовок): значение} в первый лист файла, остальное не трогая.

    names — {строка Excel: rec_name, который должен в ней стоять}; не совпало — TableSaveError.
    new_columns — {заголовок: описание} колонок, которых в файле нет: заголовок встаёт в строку 1 первой
    свободной колонки, описание — в строку 2. Книга сначала собирается в памяти и только потом пишется
    в файл одним разом: если файл открыт в Excel, он остаётся прежним.
    """
    workbook = load_workbook(path)
    sheet = workbook.worksheets[0]
    headers = [_text(c.value) for c in sheet[1]]
    for header, description in (new_columns or {}).items():
        if header in headers:
            continue
        column = max((i + 1 for i, h in enumerate(headers) if h), default=0) + 1  # после последнего заголовка
        sheet.cell(1, column).value = header
        if not _text(sheet.cell(2, column).value):
            sheet.cell(2, column).value = description
        headers += [""] * (column - len(headers))
        headers[column - 1] = header
    for header in {name_header, *(h for _, h in cells)}:
        if header not in headers:
            raise TableSaveError(f"В таблице {path.name} больше нет колонки {header}")
    name_column = headers.index(name_header) + 1
    for row, expected in names.items():
        actual = _text(sheet.cell(row, name_column).value)
        if actual != expected:
            raise TableSaveError(
                f"В строке {row} таблицы {path.name} теперь «{actual}», а не «{expected}» — таблицу правили "
                "в Excel? Перечитайте её (F5) и повторите правку.")
    for (row, header), value in cells.items():
        sheet.cell(row, headers.index(header) + 1).value = value
    buffer = io.BytesIO()
    workbook.save(buffer)
    try:
        path.write_bytes(buffer.getvalue())
    except PermissionError:
        raise TableSaveError(
            f"Не удалось записать {path.name}: таблица открыта в Excel. Закройте её в Excel "
            "и сохраните ещё раз.") from None


class EditableTable:
    """Таблица записей с несохранёнными правками и историей отмены.

    Строки — по номеру i (с 0, в порядке таблицы), колонки — по заголовку. value(i, h) — значение с учётом
    правок; set(i, h, v) — правка (значение, равное исходному, правку снимает); save — запись в файл.
    path — файл таблицы (TABLE_FILE: полный путь или от папки проекта); headers, descriptions, rows — как возвращает read_table.
    undo_stack / redo_stack — шаги отмены (последний — в конце), dirty — есть ли несохранённые правки
    (ячейки или добавленные колонки).
    """

    def __init__(self, path: Path) -> None:
        self.path = project_path(path)
        self.headers, self.descriptions, self.rows = read_table(self.path)
        self.changes: dict[tuple[int, str], Any] = {}
        self.new_columns: dict[str, str] = {}  # добавленные колонки, которых ещё нет в файле: заголовок -> описание
        self.undo_stack: list[_Step] = []
        self.redo_stack: list[_Step] = []
        self._group: _Step | None = None  # накапливаемые правки batch()
        self._depth = 0  # вложенность batch()

    def __len__(self) -> int:
        return len(self.rows)

    @property
    def dirty(self) -> bool:
        """Есть ли несохранённые правки: изменённые ячейки или добавленные колонки."""
        return bool(self.changes or self.new_columns)

    # --- значения
    def column(self, key: str) -> str | None:
        """Заголовок колонки величины key из COLUMN_NAMES (например, "rim" -> "rim" или "rim_new")."""
        return next((h for h in COLUMN_NAMES[key] if h in self.headers), None)

    def add_column(self, header: str, description: str) -> None:
        """Добавить колонку, которой нет в таблице (в файл она попадёт при save, даже пустая)."""
        if header not in self.headers:
            self.headers.append(header)
            self.descriptions[header] = description
            self.new_columns[header] = description
            self._record_column(header, description)

    def original(self, i: int, header: str) -> Any:
        """Значение ячейки в файле (без правок)."""
        return self.rows[i].values.get(header)

    def value(self, i: int, header: str) -> Any:
        """Значение ячейки с учётом правок."""
        return self.changes.get((i, header), self.original(i, header))

    def set(self, i: int, header: str, value: Any, merge: str | None = None) -> None:
        """Правка ячейки; значение, равное исходному, снимает правку.

        merge — ключ серии правок: правки с тем же ключом подряд (набор текста в поле, движение ползунка)
        становятся одним шагом отмены; break_merge заканчивает серию.
        """
        key = (i, header)
        before = self.changes.get(key, _NONE)
        if same_value(value, self.original(i, header)):
            self.changes.pop(key, None)
        else:
            self.changes[key] = value
        after = self.changes.get(key, _NONE)
        if not _same_state(before, after):
            self._record_edit(i, header, before, after, merge)

    # --- история отмены
    @contextmanager
    def batch(self) -> Iterator[None]:
        """Сгруппировать правки внутри блока в один шаг отмены (вложенные блоки — тот же шаг)."""
        self._depth += 1
        if self._depth == 1:
            self._group = _Step(None, [], [])
        try:
            yield
        finally:
            self._depth -= 1
            if self._depth == 0:
                if self._group is not None and (self._group.edits or self._group.columns):
                    self.undo_stack.append(self._group)
                    self.redo_stack.clear()
                self._group = None

    def break_merge(self) -> None:
        """Закончить серию правок с общим ключом merge: следующая правка начнёт новый шаг отмены."""
        if self.undo_stack and self.undo_stack[-1].merge is not None:
            self.undo_stack[-1].merge = None

    def _record_edit(self, i: int, header: str, before: Any, after: Any, merge: str | None) -> None:
        """Запомнить правку ячейки для отмены: в группу, в шаг серии merge или в новый шаг."""
        edit = _Edit(i, header, before, after)
        if self._group is not None:
            self._group.edits.append(edit)
            return
        if merge is not None and self.undo_stack and self.undo_stack[-1].merge == merge:
            step = self.undo_stack[-1]
            for index, old in enumerate(step.edits):
                if old.i == i and old.header == header:
                    if _same_state(old.before, after):  # набрали обратно как было — шаг не нужен
                        step.edits.pop(index)
                        if not step.edits and not step.columns:
                            self.undo_stack.pop()
                    else:
                        old.after = after  # та же ячейка в той же серии — обновляем конечное значение
                    break
            else:
                step.edits.append(edit)
            self.redo_stack.clear()
            return
        self.undo_stack.append(_Step(merge, [edit], []))
        self.redo_stack.clear()

    def _record_column(self, header: str, description: str) -> None:
        """Запомнить добавление колонки для отмены."""
        if self._group is not None:
            self._group.columns.append((header, description))
            return
        self.undo_stack.append(_Step(None, [], [(header, description)]))
        self.redo_stack.clear()

    def undo(self) -> _Step | None:
        """Отменить последний шаг; None — отменять нечего."""
        self.break_merge()
        if not self.undo_stack:
            return None
        step = self.undo_stack.pop()
        for edit in reversed(step.edits):
            if edit.before is _NONE:
                self.changes.pop((edit.i, edit.header), None)
            else:
                self.changes[(edit.i, edit.header)] = edit.before
        for header, _ in reversed(step.columns):
            if header in self.headers:
                self.headers.remove(header)
            self.descriptions.pop(header, None)
            self.new_columns.pop(header, None)
            for key in [key for key in self.changes if key[1] == header]:
                self.changes.pop(key, None)
        self.redo_stack.append(step)
        return step

    def redo(self) -> _Step | None:
        """Повторить отменённый шаг; None — повторять нечего."""
        self.break_merge()
        if not self.redo_stack:
            return None
        step = self.redo_stack.pop()
        for header, description in step.columns:
            if header not in self.headers:
                self.headers.append(header)
                self.descriptions[header] = description
                self.new_columns[header] = description
        for edit in step.edits:
            if edit.after is _NONE:
                self.changes.pop((edit.i, edit.header), None)
            else:
                self.changes[(edit.i, edit.header)] = edit.after
        self.undo_stack.append(step)
        return step

    def clear_history(self) -> None:
        """Забыть историю отмены (после save и discard отменять нечего — это уже файл)."""
        self.undo_stack.clear()
        self.redo_stack.clear()
        self._group = None
        self._depth = 0

    def changed(self, i: int, header: str | None = None) -> bool:
        """Есть ли несохранённая правка в строке i (или в её ячейке header)."""
        if header is not None:
            return (i, header) in self.changes
        return any(row == i for row, _ in self.changes)

    def name(self, i: int) -> str:
        """rec_name строки i (с учётом правок)."""
        return _text(self.value(i, self.column("rec_name")))

    # --- ввод
    def is_text(self, header: str) -> bool:
        """Текстовая ли колонка: rec_name, images_path_side или колонка, где есть только текст."""
        if header in {self.column(key) for key in REQUIRED_COLUMNS}:
            return True
        values = [r.values.get(header) for r in self.rows if not is_empty(r.values.get(header))]
        return bool(values) and all(isinstance(v, str) for v in values)

    def is_numeric(self, header: str) -> bool:
        """Числовая ли колонка: в ней есть числа и нет текста."""
        values = [r.values.get(header) for r in self.rows if not is_empty(r.values.get(header))]
        return bool(values) and all(isinstance(v, (int, float)) for v in values)

    def parse(self, header: str, text: str) -> Any:
        """Текст из поля ввода -> значение ячейки: пусто -> None, в нетекстовой колонке число -> число
        (запятая тоже годится как десятичный знак). Не число в числовой колонке — ValueError."""
        text = text.strip()
        if not text:
            return None
        if self.is_text(header):
            return text
        try:
            number = float(text.replace(",", "."))
        except ValueError:
            if self.is_numeric(header):
                raise ValueError(f"в колонке {header} должно быть число") from None
            return text
        return int(number) if number.is_integer() and abs(number) < 1e15 else number

    # --- запись таблицы как Record
    def record(self, i: int) -> Record | None:
        """Строка i как Record (с учётом правок, по тем же правилам, что read_records);
        None — нет rec_name или images_path_side."""

        def get(key: str) -> Any:
            header = self.column(key)
            return None if header is None else self.value(i, header)

        name, side = _text(get("rec_name")), _text(get("images_path_side"))
        if not name or not side:
            return None
        path = side.replace("\\", "/").rstrip("/")
        if not path.endswith("/" + name) and path != name:  # как в read_records
            path = f"{path}/{name}"
        numbers = {}
        for header in self.headers:
            value = _number(self.value(i, header))
            if value is not None:
                numbers[header] = value
        return Record(name=name, path=path, cond_num=_number(get("cond_num")),
                      start_frame=_integer(get("start_frame")), fin_frame=_integer(get("fin_frame")),
                      rim=_number(get("rim")), cx=_number(get("cx")), cy=_number(get("cy")),
                      numbers=numbers)

    # --- автозаполнение
    def filled(self, i: int) -> bool:
        """Заполнена ли запись: задан диапазон кадров."""
        record = self.record(i)
        return record is not None and record.has_frames

    def previous_filled(self, i: int) -> int | None:
        """Ближайшая выше заполненная запись; если выше нет — ближайшая ниже; нет совсем — None."""
        above = next((j for j in range(i - 1, -1, -1) if self.filled(j)), None)
        return above if above is not None else next((j for j in range(i + 1, len(self)) if self.filled(j)), None)

    def suggestion(self, i: int, header: str) -> tuple[int, Any] | None:
        """Подсказка для пустой ячейки: (строка предыдущей заполненной записи, её значение); иначе None."""
        if not is_empty(self.value(i, header)):
            return None
        j = self.previous_filled(i)
        if j is None or is_empty(self.value(j, header)):
            return None
        return j, self.value(j, header)

    def common_values(self, exclude: int | None = None) -> dict[str, Any]:
        """Колонки, значения которых одинаковы во всех заполненных записях (кроме строки exclude);
        заполненных записей должно быть хотя бы две."""
        filled = [j for j in range(len(self)) if j != exclude and self.filled(j)]
        if len(filled) < 2:
            return {}
        common = {}
        for header in self.headers:
            values = [self.value(j, header) for j in filled]
            if not is_empty(values[0]) and all(same_value(v, values[0]) for v in values):
                common[header] = values[0]
        return common

    def fill_like_previous(self, i: int) -> list[str]:
        """Заполнить пустые ячейки строки i значениями, одинаковыми во всех заполненных записях;
        возвращает заголовки заполненных колонок."""
        filled = []
        for header, value in self.common_values(exclude=i).items():
            if is_empty(self.value(i, header)):
                self.set(i, header, value)
                filled.append(header)
        return filled

    def fill_down(self, source: int, rows: list[int], headers: list[str]) -> list[tuple[int, str, Any]]:
        """Растянуть значения строки source по колонкам headers на строки rows одной правкой.

        Возвращает правки [(строка, колонка, значение)] — только ячейки, которые действительно меняются
        (source и одинаковые значения пропускаются).
        """
        cells = [(i, header, self.value(source, header))
                 for i in rows if i != source for header in headers
                 if not same_value(self.value(source, header), self.value(i, header))]
        if cells:
            with self.batch():
                for i, header, value in cells:
                    self.set(i, header, value)
        return cells

    # --- файл
    def save(self, backup_dir: Path | None = None) -> Path | None:
        """Записать правки в файл (только изменённые ячейки и добавленные колонки) и перечитать таблицу.

        backup_dir — папка, куда сначала кладётся копия файла (None — без копии). Возвращает путь копии
        (или None). Не удалось записать — TableSaveError, правки остаются несохранёнными. После записи
        история отмены очищается.
        """
        if not self.dirty:
            return None
        backup = None
        if backup_dir is not None:
            backup_dir.mkdir(parents=True, exist_ok=True)
            backup = backup_dir / f"{self.path.stem} {datetime.now():%Y-%m-%d %H-%M-%S}{self.path.suffix}"
            shutil.copy2(self.path, backup)
        name_header = self.column("rec_name")
        cells = {(self.rows[i].excel_row, header): value for (i, header), value in self.changes.items()}
        names = {self.rows[i].excel_row: _text(self.original(i, name_header)) for i, _ in self.changes}
        save_cells(self.path, cells, names, name_header, dict(self.new_columns))
        self.headers, self.descriptions, self.rows = read_table(self.path)
        self.changes, self.new_columns = {}, {}
        self.clear_history()
        return backup

    def discard(self) -> None:
        """Забыть правки: перечитать таблицу из файла и очистить историю отмены."""
        self.headers, self.descriptions, self.rows = read_table(self.path)
        self.changes, self.new_columns = {}, {}
        self.clear_history()
