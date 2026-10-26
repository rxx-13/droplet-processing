r"""Проверка, что документация не отстала от кода.

    * у каждой функции и класса в lib/, main/, tools/, app/ есть описание (docstring);
      исключения — вложенные функции-помощники и служебные методы вида __init__ (их аргументы
      описаны в описании класса);
    * каждая функция и класс из lib/ (методы — как Класс.метод) есть в таблицах CODE_GUIDE.md,
      а в первой колонке этих таблиц нет имён, которых нет в коде;
    * каждая настройка и каждый порог из experiments/*/settings.py и thresholds.py есть в CODE_GUIDE.md
      и действительно используется в коде.

Если тест упал после правки кода — допишите описание или строку в CODE_GUIDE.md.

Запуск:  .venv\Scripts\python.exe -B -m pytest tests\test_docs.py
"""

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUIDE = (ROOT / "CODE_GUIDE.md").read_text(encoding="utf-8")


def _definitions(path: Path) -> list[tuple[str, ast.AST]]:
    """Функции и классы модуля и методы классов (без вложенных функций): (имя, узел)."""
    out = []
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            out.append((node.name, node))
            if isinstance(node, ast.ClassDef):
                out += [(f"{node.name}.{m.name}", m) for m in node.body if isinstance(m, ast.FunctionDef)]
    return out


def _is_dunder(name: str) -> bool:
    last = name.split(".")[-1]
    return last.startswith("__") and last.endswith("__")


def _code_files(*folders: str) -> list[Path]:
    return sorted(p for folder in folders for p in (ROOT / folder).rglob("*.py"))


def test_every_function_and_class_has_a_description():
    missing = [f"{path.relative_to(ROOT)}: {name}"
               for path in _code_files("lib", "main", "tools", "app")
               for name, node in _definitions(path)
               if not _is_dunder(name) and not ast.get_docstring(node)]
    assert not missing, "Нет описания (docstring):\n" + "\n".join(missing)


def test_code_guide_lists_every_lib_function_and_nothing_else():
    in_code = {name for path in _code_files("lib") for name, _ in _definitions(path) if not _is_dunder(name)}
    functions_section = GUIDE[GUIDE.index("## 3.") : GUIDE.index("## 4.")]  # раздел «Функции по файлам»
    in_guide = set(re.findall(r"^\| `([A-Za-z_][\w.]*)` \|", functions_section, flags=re.M))
    assert not in_code - in_guide, f"Нет в таблицах CODE_GUIDE.md: {sorted(in_code - in_guide)}"
    assert not in_guide - in_code, f"В CODE_GUIDE.md есть, а в коде нет: {sorted(in_guide - in_code)}"


def test_every_setting_and_threshold_is_documented_and_used():
    code = "\n".join(p.read_text(encoding="utf-8") for p in _code_files("lib", "main", "tools", "tests"))
    for path in sorted((ROOT / "experiments").rglob("*.py")):
        if path.name == "current.py":
            continue
        for name in re.findall(r"^([A-Z][A-Z0-9_]+) =", path.read_text(encoding="utf-8"), flags=re.M):
            assert f"`{name}`" in GUIDE, f"{name} ({path.relative_to(ROOT)}) нет в таблице настроек CODE_GUIDE.md"
            assert re.search(rf"\b{name}\b", code), f"{name} ({path.relative_to(ROOT)}) нигде в коде не используется"
