"""Файлы настроек эксперимента (settings.py, thresholds.py) для приложения: значения с комментариями,
несохранённые правки, запись только изменённых значений; выбор текущего эксперимента (experiments/current.py).

Значение в файле — строка `ИМЯ = значение  # комментарий` (ИМЯ — заглавными буквами); значение — литерал
Python: число, строка, кортеж, список (как его читает ast.literal_eval). Разделы — строки
`# ---------- название`; комментарии прямо над строкой значения — его пояснение.

Запись меняет только текст значений изменённых строк: имена, комментарии, разделы, пустые строки и концы
строк остаются как были. Текст значения пишется так, как его набрали (0.0000001 не превращается в 1e-07);
строки с «\\» (пути) — как r"...".
"""

import ast
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SECTION = re.compile(r"^#\s*-{3,}\s*(.*?)\s*$")  # строка раздела: # ---------- название
CURRENT_IMPORT = re.compile(r"^from experiments\.(\w+) import ")  # выбор эксперимента в current.py


@dataclass(frozen=True)
class Setting:
    """Значение в файле настроек. name — имя; text — текст значения в файле; value — значение; comment —
    комментарий в конце строки; note — комментарии над строкой; section — раздел файла; start, end —
    где в файле текст значения: (номер строки с 0, номер символа в строке)."""

    name: str
    text: str
    value: Any
    comment: str
    note: str
    section: str
    start: tuple[int, int]
    end: tuple[int, int]


def _char_column(line: str, byte_column: int) -> int:
    """Номер символа в строке по смещению в байтах UTF-8 (так считает ast)."""
    return len(line.encode("utf-8")[:byte_column].decode("utf-8"))


def literal(text: str) -> Any:
    """Текст литерала Python -> значение; не литерал — ValueError с понятным текстом."""
    try:
        return ast.literal_eval(text.strip())
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        raise ValueError(f"«{text.strip()}» — не число, не строка, не кортеж и не список") from None


def parse_settings(source: str) -> list[Setting]:
    """Значения `ИМЯ = литерал` верхнего уровня из текста файла настроек (всё остальное пропускается)."""
    lines = source.splitlines()
    sections, section = [], ""
    for line in lines:
        match = SECTION.match(line.strip())
        section = match.group(1) if match else section
        sections.append(section)
    found = []
    for node in ast.parse(source).body:
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id.isupper()):
            continue
        v = node.value
        try:
            value = ast.literal_eval(v)
        except (ValueError, TypeError, SyntaxError):
            continue  # не литерал (выражение) — в приложении не правится
        start = (v.lineno - 1, _char_column(lines[v.lineno - 1], v.col_offset))
        end = (v.end_lineno - 1, _char_column(lines[v.end_lineno - 1], v.end_col_offset))
        rest = lines[end[0]][end[1]:].strip()
        note, k = [], node.lineno - 2
        while k >= 0 and lines[k].strip().startswith("#") and not SECTION.match(lines[k].strip()):
            note.insert(0, lines[k].strip().lstrip("#").strip())
            k -= 1
        found.append(Setting(name=node.targets[0].id, text=ast.get_source_segment(source, v), value=value,
                             comment=rest[1:].strip() if rest.startswith("#") else "", note=" ".join(note),
                             section=sections[node.lineno - 1], start=start, end=end))
    return found


def value_text(value: Any) -> str:
    """Значение -> текст литерала для файла настроек: строки — в двойных кавычках, с «\\» — как r"..."."""
    if isinstance(value, str) and '"' not in value and "\n" not in value:
        if "\\" not in value:
            return f'"{value}"'
        if not value.endswith("\\"):
            return f'r"{value}"'
    return repr(value)


def kind_of(value: Any) -> str:
    """Вид значения словами: число, строка, кортеж, список."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return "число"
    return {str: "строка", tuple: "кортеж (…)", list: "список […]"}.get(type(value), type(value).__name__)


class SettingsFile:
    """Файл настроек (settings.py или thresholds.py) с несохранёнными правками.

    Правка — новый текст значения (set_text); значения с учётом правок — values(); save пишет в файл
    только изменённые значения. path — путь к файлу.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._read()

    def _read(self) -> None:
        """Прочитать файл; правки забываются."""
        self.source = self.path.read_bytes().decode("utf-8")
        self.settings = {s.name: s for s in parse_settings(self.source)}
        self.edits: dict[str, str] = {}

    def text(self, name: str) -> str:
        """Текст значения name с учётом правок."""
        return self.edits.get(name, self.settings[name].text)

    def value(self, name: str) -> Any:
        """Значение name с учётом правок."""
        return literal(self.text(name))

    def values(self) -> dict[str, Any]:
        """Все значения файла с учётом правок."""
        return {name: self.value(name) for name in self.settings}

    def set_text(self, name: str, text: str) -> Any:
        """Новый текст значения name. Должен быть литералом того же вида, что в файле (число, строка,
        кортеж, список), иначе ValueError с понятным текстом (правка тогда не меняется). Возвращает значение."""
        value = literal(text)
        old = self.settings[name].value
        if kind_of(value) != kind_of(old):
            raise ValueError(f"{name}: нужно {kind_of(old)}, а введено {kind_of(value)}")
        if text.strip() == self.settings[name].text:
            self.edits.pop(name, None)
        else:
            self.edits[name] = text.strip()
        return value

    def changed(self, name: str | None = None) -> bool:
        """Есть ли несохранённые правки (у значения name или во всём файле)."""
        return name in self.edits if name is not None else bool(self.edits)

    def discard(self) -> None:
        """Забыть правки."""
        self.edits.clear()

    def save(self) -> list[str]:
        """Записать правки в файл — меняются только тексты изменённых значений — и перечитать его.
        Возвращает имена записанных значений."""
        if not self.edits:
            return []
        lines = self.source.splitlines(keepends=True)
        for name in sorted(self.edits, key=lambda n: self.settings[n].start, reverse=True):
            (first, start), (last, end) = self.settings[name].start, self.settings[name].end
            lines[first : last + 1] = [lines[first][:start] + self.edits[name] + lines[last][end:]]
        source = "".join(lines)
        written = {s.name: s.value for s in parse_settings(source)}
        for name, text in self.edits.items():  # проверка: файл читается, и в нём новые значения
            if written.get(name) != literal(text):
                raise ValueError(f"{name}: не удалось записать значение {text}")
        self.path.write_bytes(source.encode("utf-8"))
        saved = list(self.edits)
        self._read()
        return saved


def current_experiment(current_file: Path) -> str | None:
    """Эксперимент (имя папки в experiments/), выбранный в experiments/current.py; None — не выбран."""
    if not current_file.exists():
        return None
    for line in current_file.read_text(encoding="utf-8").splitlines():
        match = CURRENT_IMPORT.match(line)
        if match:
            return match.group(1)
    return None


def set_current_experiment(current_file: Path, name: str) -> None:
    """Выбрать эксперимент name в experiments/current.py: строка импорта заменяется на
    `from experiments.<name> import settings, thresholds`, комментарии остаются."""
    lines = current_file.read_bytes().decode("utf-8").splitlines(keepends=True) if current_file.exists() else []
    line = f"from experiments.{name} import settings, thresholds"
    for i, old in enumerate(lines):
        if CURRENT_IMPORT.match(old):
            lines[i] = line + (old[len(old.rstrip("\r\n")):] or "\n")
            break
    else:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.append(line + "\n")
    current_file.write_bytes("".join(lines).encode("utf-8"))
