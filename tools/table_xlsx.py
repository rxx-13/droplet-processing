r"""Просмотр и правка таблиц записей xlsx без приложения (для Claude Code).

    .venv\Scripts\python.exe tools\table_xlsx.py show <файл.xlsx> [--columns fps,rim] [--record ИМЯ] [--limit N]
    .venv\Scripts\python.exe tools\table_xlsx.py set <файл.xlsx> <rec_name> <колонка> <значение>
    .venv\Scripts\python.exe tools\table_xlsx.py add-column <файл.xlsx> <колонка> <описание>

Путь — полный или от папки проекта. Чтение и запись — по тем же правилам, что у приложения
(lib/common/table_edit.py): правится только указанная ячейка, остальное (формат, другие ячейки, ширина
колонок) остаётся; перед записью копия файла кладётся в <папка таблицы>/table_backups. Если файл открыт
в Excel, запись не выполняется и печатается понятная ошибка.

Команда нужна, чтобы Claude Code мог смотреть и править таблицы, не получая общего доступа к Python:
в настройках разрешён только вызов этого файла.
"""

import sys
from pathlib import Path

sys.dont_write_bytecode = True  # не создавать папки __pycache__
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # чтобы находились lib и experiments

import argparse  # noqa: E402
from typing import Any  # noqa: E402

from lib.common.records import project_path  # noqa: E402
from lib.common.table_edit import EditableTable, TableSaveError, format_value  # noqa: E402

for _stream in (sys.stdout, sys.stderr):  # русский текст в Git Bash и при перенаправлении вывода
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")


def _table(file: str) -> tuple[Path, EditableTable]:
    """Открыть таблицу xlsx (путь полный или от папки проекта) и вернуть её путь и EditableTable."""
    path = project_path(file)
    if path.suffix.lower() != ".xlsx":
        raise SystemExit(f"Это не файл xlsx: {path}")
    if not path.is_file():
        raise SystemExit(f"Файл не найден: {path}")
    return path, EditableTable(path)


def _find(table: EditableTable, record: str) -> int:
    """Номер строки (с 0) записи rec_name; если такой записи нет — понятная ошибка."""
    for i in range(len(table)):
        if table.name(i) == record:
            return i
    names = ", ".join(table.name(i) for i in range(min(len(table), 10)))
    raise SystemExit(f"Запись «{record}» не найдена. Первые записи: {names}")


def _clean(value: Any) -> str:
    """Значение ячейки как текст для вывода: табуляции и переводы строк заменяются пробелами."""
    return format_value(value).replace("\t", " ").replace("\r", " ").replace("\n", " ")


def _headers(table: EditableTable, columns: str | None) -> list[str]:
    """Колонки для вывода: все или перечисленные через запятую (проверяются по таблице)."""
    if not columns:
        return list(table.headers)
    wanted = [name.strip() for name in columns.split(",") if name.strip()]
    missing = [name for name in wanted if name not in table.headers]
    if missing:
        raise SystemExit(f"Нет колонок {', '.join(missing)}. Есть: {', '.join(table.headers)}")
    return wanted


def show(table: EditableTable, columns: str | None = None, record: str | None = None, limit: int = 0) -> None:
    """Напечатать таблицу: заголовки с описаниями и строки; одна запись — «колонка = значение»."""
    headers = _headers(table, columns)
    for header in headers:
        description = table.descriptions.get(header, "")
        print(f"# {header}: {description}" if description else f"# {header}")
    if record is not None:
        i = _find(table, record)
        print(f"# запись {record}, строка Excel {table.rows[i].excel_row}")
        for header in headers:
            print(f"{header} = {_clean(table.value(i, header))}")
        return
    other = [header for header in headers if header != "rec_name"]  # rec_name уже есть отдельной колонкой
    print("\t".join(["row", "rec_name", *other]))
    rows = table.rows if limit <= 0 else table.rows[:limit]
    for i, row in enumerate(rows):
        values = [_clean(table.value(i, header)) for header in other]
        print("\t".join([str(row.excel_row), table.name(i), *values]))


def set_value(table: EditableTable, path: Path, record: str, column: str, text: str) -> None:
    """Изменить одну ячейку записи и записать таблицу (только изменённые ячейки, с копией файла)."""
    if column not in table.headers:
        raise SystemExit(f"Нет колонки {column}. Есть: {', '.join(table.headers)}")
    i = _find(table, record)
    before = table.value(i, column)
    value = table.parse(column, text)
    table.set(i, column, value)
    if not table.dirty:
        print(f"{record}.{column}: без изменений ({_clean(before)})")
        return
    backup = table.save(backup_dir=path.parent / "table_backups")
    print(f"{record}.{column}: {_clean(before)} -> {_clean(value)}")
    if backup is not None:
        print(f"копия: {backup}")


def add_column(table: EditableTable, path: Path, header: str, description: str) -> None:
    """Добавить колонку (заголовок в строку 1, описание в строку 2) и записать файл."""
    if header in table.headers:
        raise SystemExit(f"Колонка {header} уже есть")
    table.add_column(header, description)
    backup = table.save(backup_dir=path.parent / "table_backups")
    print(f"добавлена колонка {header}: {description}")
    if backup is not None:
        print(f"копия: {backup}")


def main(argv: list[str] | None = None) -> int:
    """Разобрать аргументы и выполнить команду show / set / add-column."""
    parser = argparse.ArgumentParser(description="Просмотр и правка таблиц записей xlsx.")
    commands = parser.add_subparsers(dest="command", required=True)

    p_show = commands.add_parser("show", help="напечатать таблицу")
    p_show.add_argument("file", help="файл .xlsx (полный путь или от папки проекта)")
    p_show.add_argument("--columns", help="колонки через запятую (по умолчанию все)")
    p_show.add_argument("--record", help="только одна запись rec_name")
    p_show.add_argument("--limit", type=int, default=0, help="не больше N строк (0 — все)")

    p_set = commands.add_parser("set", help="изменить одну ячейку")
    p_set.add_argument("file")
    p_set.add_argument("record", help="rec_name записи")
    p_set.add_argument("column", help="заголовок колонки")
    p_set.add_argument("value", help="новое значение (пусто — очистить ячейку)")

    p_add = commands.add_parser("add-column", help="добавить колонку")
    p_add.add_argument("file")
    p_add.add_argument("header", help="заголовок новой колонки")
    p_add.add_argument("description", help="описание во вторую строку таблицы")

    args = parser.parse_args(argv)
    path, table = _table(args.file)
    try:
        if args.command == "show":
            show(table, args.columns, args.record, args.limit)
        elif args.command == "set":
            set_value(table, path, args.record, args.column, args.value)
        else:
            add_column(table, path, args.header, args.description)
    except TableSaveError as error:
        print(error, file=sys.stderr)
        return 1
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
