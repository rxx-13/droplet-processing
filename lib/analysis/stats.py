"""Обработка результатов: выборка по условиям, распределения (гистограммы) и зависимости по окнам аргумента.

Выборка — строки, удовлетворяющие всем условиям (Condition) сразу: колонка равна одному из выбранных
значений и/или лежит в диапазоне от–до (границы включаются). Окна — равные отрезки по аргументу
(window_edges: ширина или число окон, пределы; по умолчанию — от минимума до максимума). В каждом окне:
    распределение (histogram)   число строк; доля = число / всего; плотность = доля / ширина окна;
    зависимость y(x) (binned)   статистика y по строкам, у которых x в окне (STATISTICS): среднее ± СКО,
                                среднее ± ошибка среднего (СКО / √n), медиана с квартилями (25 % и 75 %), число.
Строка с x на правой границе последнего окна относится к нему; вне окон — не учитывается.
Серии (split) — то же отдельно для каждого значения выбранной колонки (например, F); окна у всех серий одни.
"""

from dataclasses import dataclass
from typing import Any

import numpy as np

from .dataset import DataTable

STATISTICS = {"mean_std": "среднее ± СКО", "mean_sem": "среднее ± ошибка среднего",
              "median": "медиана, квартили", "count": "число"}
NORMS = {"count": "число", "fraction": "доля", "density": "плотность (доля / ширина окна)"}
DEFAULT_WINDOWS = 20  # число окон, если ни ширина, ни число не заданы


@dataclass(frozen=True)
class Condition:
    """Условие выборки: column — колонка; values — допустимые значения (пусто — не проверяются);
    low, high — границы диапазона, включительно (None — без границы; только для чисел)."""

    column: str
    values: tuple = ()
    low: float | None = None
    high: float | None = None

    def mask(self, table: DataTable) -> np.ndarray:
        """Какие строки таблицы удовлетворяют условию."""
        data = table.columns[self.column]
        keep = np.ones(len(table), dtype=bool)
        if self.values:
            if table.is_numeric(self.column):
                keep &= np.isin(data, np.array(self.values, dtype=np.float64))
            else:
                keep &= np.isin(data.astype(str), [str(v) for v in self.values])
        if table.is_numeric(self.column):
            with np.errstate(invalid="ignore"):
                if self.low is not None:
                    keep &= data >= self.low
                if self.high is not None:
                    keep &= data <= self.high
        return keep


def select(table: DataTable, conditions: list[Condition]) -> DataTable:
    """Строки, удовлетворяющие всем условиям."""
    keep = np.ones(len(table), dtype=bool)
    for condition in conditions:
        keep &= condition.mask(table)
    return table.take(keep)


def window_edges(values: np.ndarray, width: float | None = None, count: int | None = None,
                 low: float | None = None, high: float | None = None) -> np.ndarray:
    """Границы окон: от low до high (по умолчанию — минимум и максимум values) шириной width или числом count
    (по умолчанию DEFAULT_WINDOWS); при заданной ширине последнее окно доходит до high или чуть дальше."""
    finite = np.asarray(values, dtype=np.float64)
    finite = finite[np.isfinite(finite)]
    lo = low if low is not None else (float(finite.min()) if finite.size else 0.0)
    hi = high if high is not None else (float(finite.max()) if finite.size else 1.0)
    if hi <= lo:
        hi = lo + (width or 1.0)
    if width:
        n = max(1, int(np.ceil((hi - lo) / width - 1e-9)))
        return lo + width * np.arange(n + 1)
    return np.linspace(lo, hi, (count or DEFAULT_WINDOWS) + 1)


@dataclass
class Series:
    """Посчитанная кривая: label — серия («» — без серий); x, y — значения (у окон x — середина окна);
    low, high — нижняя и верхняя граница разброса y (None — нет); count — строк в окне; edges — границы
    окон (None — точки без окон)."""

    label: str
    x: np.ndarray
    y: np.ndarray
    low: np.ndarray | None = None
    high: np.ndarray | None = None
    count: np.ndarray | None = None
    edges: np.ndarray | None = None

    def columns(self) -> dict[str, np.ndarray]:
        """Таблица кривой для экспорта: x, y, границы разброса, число, границы окон (что есть)."""
        found = {"x": self.x, "y": self.y}
        if self.low is not None:
            found.update({"y_low": self.low, "y_high": self.high})
        if self.count is not None:
            found["count"] = self.count.astype(np.float64)
        if self.edges is not None:
            found.update({"window_from": self.edges[:-1], "window_to": self.edges[1:]})
        return found


def _window_index(x: np.ndarray, edges: np.ndarray) -> np.ndarray:
    """Номер окна каждого x (с 0); вне окон — −1. Правая граница последнего окна — в последнее окно."""
    index = np.searchsorted(edges, x, side="right") - 1
    index[x == edges[-1]] = len(edges) - 2
    index[(x < edges[0]) | (x > edges[-1]) | ~np.isfinite(x)] = -1
    return index


def histogram(values: np.ndarray, edges: np.ndarray, norm: str = "count", label: str = "") -> Series:
    """Распределение values по окнам edges: число, доля или плотность (NORMS)."""
    index = _window_index(np.asarray(values, dtype=np.float64), edges)
    counts = np.bincount(index[index >= 0], minlength=len(edges) - 1)
    y = counts.astype(np.float64)
    total = counts.sum()
    if norm in ("fraction", "density"):
        y = y / total if total else y
    if norm == "density":
        y = y / np.diff(edges)
    return Series(label, (edges[:-1] + edges[1:]) / 2, y, count=counts, edges=edges)


def binned(x: np.ndarray, y: np.ndarray, edges: np.ndarray, statistic: str = "mean_std", label: str = "") -> Series:
    """Зависимость y от x по окнам x: статистика y в каждом окне (STATISTICS); пустое окно — NaN."""
    x, y = np.asarray(x, dtype=np.float64), np.asarray(y, dtype=np.float64)
    index = _window_index(x, edges)
    index[~np.isfinite(y)] = -1
    n = len(edges) - 1
    values, low, high = np.full(n, np.nan), np.full(n, np.nan), np.full(n, np.nan)
    counts = np.bincount(index[index >= 0], minlength=n)
    for k in range(n):
        v = y[index == k]
        if not v.size:
            continue
        if statistic == "median":
            values[k] = np.median(v)
            low[k], high[k] = np.percentile(v, [25, 75])
        elif statistic == "count":
            values[k] = v.size
        else:
            spread = v.std(ddof=1) if v.size > 1 else 0.0
            if statistic == "mean_sem":
                spread /= np.sqrt(v.size)
            values[k] = v.mean()
            low[k], high[k] = values[k] - spread, values[k] + spread
    if statistic == "count":
        values = counts.astype(np.float64)
        return Series(label, (edges[:-1] + edges[1:]) / 2, values, count=counts, edges=edges)
    return Series(label, (edges[:-1] + edges[1:]) / 2, values, low, high, counts, edges)


def points(x: np.ndarray, y: np.ndarray, label: str = "") -> Series:
    """Точки (x, y) как есть (строки с NaN отбрасываются)."""
    x, y = np.asarray(x, dtype=np.float64), np.asarray(y, dtype=np.float64)
    ok = np.isfinite(x) & np.isfinite(y)
    return Series(label, x[ok], y[ok])


def label_value(value: Any) -> str:
    """Значение для подписи серии: целые числа — без «.0»."""
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else f"{value:g}"
    return str(value)


def split(table: DataTable, by: str | None) -> list[tuple[str, DataTable]]:
    """Серии: [(подпись, строки)] по значениям колонки by; None — одна серия без подписи."""
    if not by:
        return [("", table)]
    column = table.columns[by]
    numeric = table.is_numeric(by)
    groups = []
    for value in table.unique(by):
        mask = column == value if numeric else column.astype(str) == value
        groups.append((f"{by} = {label_value(value)}", table.take(mask)))
    return groups
