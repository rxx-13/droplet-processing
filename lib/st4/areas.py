"""st4: траектории в прямоугольных областях поиска и их средние характеристики
(порт st4_202210_droplets_in_area.m и st4_202210_droplets_in_area_v1_function2.m).

Область поиска — прямоугольник: по x — AREA_X, по y — от (нижний край − высота) до нижнего края,
px от верха кадра. Для каждой траектории, задевающей область, считаются средние по её точкам
внутри: положение, скорость (разность соседних точек / разность кадров) и радиус.

Единицы. Положение пересчитывается в мм от начала координат (cx, cy из таблицы; ось y вверх),
скорость — в м/с, радиус — в мкм.
"""

import warnings
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from matplotlib.axes import Axes
from matplotlib.collections import LineCollection
from matplotlib.figure import Figure


@dataclass(frozen=True)
class Area:
    """Область поиска, px: x = (левый, правый), y = (верхний, нижний) край от верха кадра."""

    x: tuple[float, float]
    y: tuple[float, float]


# ------------------------------------------------------------------ единицы


def px_to_mm(xy: np.ndarray, scale: float, cx: float, cy: float) -> np.ndarray:
    """Положение, px (x вправо, y вниз) -> мм от точки (cx, cy), y вверх."""
    xy = np.atleast_2d(np.asarray(xy, dtype=np.float64))
    return scale * np.column_stack([xy[:, 0] - cx, cy - xy[:, 1]])


def px_per_frame_to_m_per_s(dxy: np.ndarray, scale: float, fps: float) -> np.ndarray:
    """Смещение за кадр, px -> скорость, м/с (y вверх)."""
    d = np.atleast_2d(np.asarray(dxy, dtype=np.float64))
    return np.column_stack([d[:, 0], -d[:, 1]]) * scale / 1000.0 * fps


# ------------------------------------------------------------------ траектории в области


def segments_intersect(seg1: np.ndarray, seg2: np.ndarray) -> np.ndarray:
    """Матрица пересечений отрезков (lineSegmentIntersect.m): seg1 (N1, 4), seg2 (N2, 4) —
    строки x1 y1 x2 y2. Концы включаются, параллельные отрезки не пересекаются."""
    a = np.atleast_2d(np.asarray(seg1, dtype=np.float64))
    b = np.atleast_2d(np.asarray(seg2, dtype=np.float64))
    x1, y1, x2, y2 = (a[:, k][:, None] for k in range(4))
    x3, y3, x4, y4 = (b[:, k][None, :] for k in range(4))
    numerator_a = (x4 - x3) * (y1 - y3) - (y4 - y3) * (x1 - x3)
    numerator_b = (x2 - x1) * (y1 - y3) - (y2 - y1) * (x1 - x3)
    denominator = (y4 - y3) * (x2 - x1) - (x4 - x3) * (y2 - y1)
    with np.errstate(divide="ignore", invalid="ignore"):
        u_a = numerator_a / denominator
        u_b = numerator_b / denominator
    return (u_a >= 0) & (u_a <= 1) & (u_b >= 0) & (u_b <= 1)


def tracks_in_area(tracks: list[np.ndarray], area: Area) -> tuple[np.ndarray, list[np.ndarray]]:
    """Средние по траекториям, задевающим область.

    Возвращает:
        means (M, 5): x, y (px), dx, dy (px/кадр), радиус (px) — по одной строке на траекторию;
        inside: для каждой траектории — маска её точек, отнесённых к области (для графика).

    ВОПРОС К СПЕЦИАЛИСТУ (Q10, см. QUESTIONS.md). Как в MATLAB: точка внутри — при строгих неравенствах; отрезок, пересекающий границу,
    добавляет оба своих конца; если траектория заходит в область дважды, разность берётся
    и между последней точкой первого захода и первой точкой второго.
    """
    (x0, x1), (y0, y1) = area.x, area.y
    borders = np.array([[x0, y0, x0, y1], [x1, y0, x1, y1], [x0, y0, x1, y0], [x0, y1, x1, y1]])
    means: list[list[float]] = []
    inside: list[np.ndarray] = []
    for track in tracks:
        if track.shape[0] < 2:
            inside.append(np.zeros(track.shape[0], dtype=bool))
            continue
        frame, x, y, r = track[:, 0], track[:, 3], track[:, 4], track[:, 5]
        mask = (x > x0) & (x < x1) & (y > y0) & (y < y1)
        crossing = segments_intersect(np.column_stack([x[:-1], y[:-1], x[1:], y[1:]]), borders).any(axis=1)
        mask[:-1] |= crossing
        mask[1:] |= crossing
        inside.append(mask)
        if mask.any():
            frames_between = np.diff(frame[mask])
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)  # одна точка: среднее пустого = NaN, как в MATLAB
                means.append([x[mask].mean(), y[mask].mean(),
                              np.mean(np.diff(x[mask]) / frames_between),
                              np.mean(np.diff(y[mask]) / frames_between), r[mask].mean()])
    return (np.array(means) if means else np.zeros((0, 5))), inside


def area_file_name(height_mm: float, low_edge_mm: float) -> str:
    """Имя файла области (без расширения), как в MATLAB."""
    return (f"visota_oblasti_poiska {height_mm:.1f} mm,  "
            f"pologenie_nizhnego_kraya_oblasti_po_visote_ot_poverhnosti  {low_edge_mm:.1f} mm")


# ------------------------------------------------------------------ график


def draw_area(top: Axes, bottom: Axes, tracks: list[np.ndarray], inside: list[np.ndarray], means: np.ndarray,
              area: Area, scale: float, cx: float, cy: float, arrow_scale: float,
              y_limits_mm: tuple[float, float]) -> None:
    """Область на двух осях: top — траектории (части в области — красным), bottom — средние
    положения и скорости (зелёные точки и стрелки). Оси — мм."""
    full, parts = [], []
    for track, mask in zip(tracks, inside):
        if track.shape[0] < 2:
            continue
        xy = px_to_mm(track[:, 3:5], scale, cx, cy)
        full.append(xy)
        if mask.any():
            parts.append(xy[mask])
    for ax in (top, bottom):
        ax.add_collection(LineCollection(full, colors="k", linewidths=1.0))
    top.add_collection(LineCollection(parts, colors="r", linewidths=1.0))

    for i in range(means.shape[0]):
        dx, dy = means[i, 2], -means[i, 3]
        if dx < 0 and dy < 0:  # как в MATLAB: летящие влево-вниз не рисуются
            continue
        x, y = px_to_mm(means[i : i + 1, 0:2], scale, cx, cy)[0]
        bottom.plot(x, y, ".g", markersize=20)
        bottom.plot([x, x + dx * arrow_scale], [y, y + dy * arrow_scale], "-g", linewidth=2)

    corners = px_to_mm(np.column_stack([area.x, area.y]), scale, cx, cy)
    xs, ys = corners[:, 0], corners[:, 1]
    for ax in (top, bottom):
        for xx in xs:
            ax.plot([xx, xx], [ys[0], ys[1]], "r")
        for yy in ys:
            ax.plot([xs[0], xs[1]], [yy, yy], "r")
        ax.set_aspect("equal")
        ax.set_xlabel("x, mm", fontsize=18)
        ax.set_ylabel("y, mm", fontsize=18)
        ax.set_ylim(*y_limits_mm)
        ax.autoscale_view(scaley=False)


def plot_area(path: Path, tracks: list[np.ndarray], inside: list[np.ndarray], means: np.ndarray, area: Area,
              scale: float, cx: float, cy: float, arrow_scale: float, y_limits_mm: tuple[float, float]) -> None:
    """Картинка области (draw_area) в файл, 2400×1800 px."""
    fig = Figure(figsize=(12, 9))
    top, bottom = fig.subplots(2, 1)
    draw_area(top, bottom, tracks, inside, means, area, scale, cx, cy, arrow_scale, y_limits_mm)
    fig.savefig(path, dpi=200)
