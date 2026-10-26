"""Обработка результатов: свои формулы — вычисляемые колонки, своя кривая и подгонка параметров (как в LabPlot).

Вычисляемые колонки (computed_columns) — строки «имя = формула» по колонкам данных. Формула — выражение
как в Python: числа, имена колонок (или x для кривой), параметры, + − * / ** и функции
FUNCTIONS (sqrt, exp, log, sin, …, pi). Например: колонка «U = 0.07*F», кривая «a*x**b + c»,
распределение «A*exp(-(x - m)**2/(2*s**2))». Перед вычислением формула проверяется: разрешены только
перечисленные функции и арифметика (никаких других команд), поэтому формулой нельзя ничего испортить.

Подгонка (fit): параметры — все имена кривой, кроме x и функций; начальные значения задаются (по умолчанию 1).
Метод наименьших квадратов (scipy.optimize.curve_fit): минимизируется сумма (y − f(x))², погрешности
параметров — корни из диагонали ковариационной матрицы; R² = 1 − Σ(y − f)² / Σ(y − ȳ)².
"""

import ast
import warnings
from dataclasses import dataclass

import numpy as np
from scipy.optimize import curve_fit
from scipy.special import erf

from .dataset import DataTable

FUNCTIONS = {"sqrt": np.sqrt, "exp": np.exp, "log": np.log, "log10": np.log10, "sin": np.sin, "cos": np.cos,
             "tan": np.tan, "arcsin": np.arcsin, "arccos": np.arccos, "arctan": np.arctan, "arctan2": np.arctan2,
             "sinh": np.sinh, "cosh": np.cosh, "tanh": np.tanh, "abs": np.abs, "sign": np.sign, "erf": erf,
             "minimum": np.minimum, "maximum": np.maximum, "where": np.where, "pi": np.pi, "e": np.e}
ALLOWED_NODES = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Call, ast.Name, ast.Load, ast.Constant, ast.Compare,
                 ast.BoolOp, ast.IfExp, ast.operator, ast.unaryop, ast.cmpop, ast.boolop)


def parse(expression: str) -> ast.Expression:
    """Проверенная формула: только арифметика, сравнения, имена и вызовы функций FUNCTIONS; иначе ValueError
    с понятным текстом."""
    try:
        tree = ast.parse(expression.strip(), mode="eval")
    except SyntaxError as error:
        raise ValueError(f"формула не читается: {error.msg}") from None
    for node in ast.walk(tree):
        if not isinstance(node, ALLOWED_NODES):
            raise ValueError(f"в формуле нельзя: {type(node).__name__}")
        if isinstance(node, ast.Call) and not (isinstance(node.func, ast.Name) and node.func.id in FUNCTIONS):
            raise ValueError("в формуле можно вызывать только функции: "
                             + ", ".join(sorted(name for name, f in FUNCTIONS.items() if callable(f))))
        if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float)):
            raise ValueError("в формуле — только числа")
    return tree


def names_in(expression: str) -> list[str]:
    """Имена в формуле (колонки, x, параметры) в порядке, в каком они написаны, без функций."""
    found = sorted((node.col_offset, node.id) for node in ast.walk(parse(expression)) if isinstance(node, ast.Name))
    return list(dict.fromkeys(name for _, name in found if name not in FUNCTIONS))


def evaluate(expression: str, variables: dict[str, np.ndarray | float]) -> np.ndarray:
    """Значение формулы при значениях имён variables (массивы — поэлементно). Неизвестное имя — ValueError."""
    tree = parse(expression)
    missing = [name for name in names_in(expression) if name not in variables]
    if missing:
        raise ValueError(f"неизвестные имена: {', '.join(missing)}")
    with np.errstate(all="ignore"):
        value = eval(compile(tree, "<формула>", "eval"), {"__builtins__": {}}, {**FUNCTIONS, **variables})
    return np.asarray(value, dtype=np.float64)


def parameters_of(expression: str, argument: str = "x") -> list[str]:
    """Параметры кривой: имена формулы, кроме аргумента и функций."""
    return [name for name in names_in(expression) if name != argument]


@dataclass
class FitResult:
    """Подгонка: values, errors — параметры и их погрешности (σ); r2 — коэффициент детерминации; points —
    число точек; ok — удалось ли; message — что пошло не так."""

    values: dict[str, float]
    errors: dict[str, float]
    r2: float
    points: int
    ok: bool = True
    message: str = ""


def fit(expression: str, x: np.ndarray, y: np.ndarray, initial: dict[str, float]) -> FitResult:
    """Подогнать параметры кривой y = f(x) (expression) к точкам (x, y); initial — начальные значения
    (не заданные — 1). Точки с NaN не учитываются."""
    names = parameters_of(expression)
    x, y = np.asarray(x, dtype=np.float64), np.asarray(y, dtype=np.float64)
    ok = np.isfinite(x) & np.isfinite(y)
    x, y = x[ok], y[ok]
    start = [float(initial.get(name, 1.0)) for name in names]
    if not names:
        return FitResult({}, {}, np.nan, len(x), False, "в формуле нет параметров — подгонять нечего")
    if len(x) < len(names):
        return FitResult(dict(zip(names, start)), {}, np.nan, len(x), False,
                         f"точек ({len(x)}) меньше, чем параметров ({len(names)})")

    def model(xs: np.ndarray, *values: float) -> np.ndarray:
        return np.broadcast_to(evaluate(expression, {"x": xs, **dict(zip(names, values))}), xs.shape)

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            values, covariance = curve_fit(model, x, y, p0=start, maxfev=20000)
    except (RuntimeError, ValueError, TypeError) as error:
        return FitResult(dict(zip(names, start)), {}, np.nan, len(x), False, f"подгонка не сошлась: {error}")
    residual = y - model(x, *values)
    total = np.sum((y - y.mean()) ** 2)
    r2 = float(1 - np.sum(residual**2) / total) if total > 0 else np.nan
    errors = np.sqrt(np.abs(np.diag(covariance))) if np.all(np.isfinite(covariance)) else np.full(len(names), np.nan)
    return FitResult(dict(zip(names, map(float, values))), dict(zip(names, map(float, errors))), r2, len(x))


def computed_columns(table: DataTable, text: str) -> tuple[DataTable, list[str]]:
    """Вычисляемые колонки: по строке «имя = формула  # подпись» (пустые строки пропускаются; подпись после #
    необязательна — она идёт на оси); в формуле — числовые колонки таблицы и колонки, вычисленные строками
    выше. Возвращает таблицу с новыми колонками и список ошибок (строка с ошибкой пропускается)."""
    columns, labels, errors = dict(table.columns), dict(table.labels), []
    for number, line in enumerate(text.splitlines(), start=1):
        line, _, comment = line.partition("#")
        line = line.strip()
        if not line:
            continue
        name, separator, expression = line.partition("=")
        name = name.strip()
        if not separator or not name.isidentifier() or name in FUNCTIONS:
            errors.append(f"строка {number}: нужно «имя = формула», например U = 0.07*F")
            continue
        if name in table.columns:
            errors.append(f"строка {number}: колонка {name} уже есть")
            continue
        try:
            value = evaluate(expression, {n: c for n, c in columns.items() if c.dtype != object})
        except ValueError as error:
            errors.append(f"строка {number}: {error}")
            continue
        columns[name] = np.broadcast_to(value, (len(table),)).astype(np.float64)
        labels[name] = comment.strip()
    return DataTable(columns, labels), errors
