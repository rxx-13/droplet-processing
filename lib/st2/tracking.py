"""st2: PTV-трекинг — связывание капель соседних кадров в траектории (порт st2_PTV_mod2.m).

Трекинг идёт назад по времени: от конца записи к началу. Для каждой траектории предсказывается,
где капля была на кадре i:
    * по PIV: последняя точка минус скорость PIV × число кадров;
    * по самой траектории: полином (степень 1 по 3 точкам, 2 по 4 и более) по последним точкам.
Берётся ближайшая к предсказанию капля, если она:
    * ближе радиуса поиска (он сужается, когда полином предсказывает точно);
    * не сильно отклоняется от предсказания полинома (порог max_deviation);
    * лежит почти на одной прямой с двумя последними точками (порог min_straightness);
    * похожа по размеру (отношение радиусов в пределах max_radius_ratio).
Если каплю уже взяла другая траектория, её получает та, у которой больше точек.
Капли, не попавшие ни в одну траекторию, начинают новые.

Траектория — матрица k×16, строка 1 — самый поздний кадр. Колонки (номера как в MATLAB):
    1 кадр, 2 номер капли на кадре, 3 расстояние от предсказанного положения до капли, 4–5 x, y, 6 радиус,
    7–8 скорость PIV xd, yd, 9–10 предсказание по PIV, 11–12 предсказание по полиному,
    13–14 итоговое предсказание, 15 погрешность полинома, 16 радиус поиска.
Номера кадров и капель — с 1, координаты — px с 1 (как в particles.mat).
"""

import math
from pathlib import Path

import numpy as np
from matplotlib.axes import Axes
from matplotlib.collections import LineCollection
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from scipy.linalg import solve_triangular

from ..common import files

NAN = math.nan


# ------------------------------------------------------------------ запись и картинка


def save_tracks(st2_dir: Path, tracks: list[np.ndarray]) -> None:
    """Траектории в ptv_unfiltered.mat (переменная p_arr — ячейка MATLAB, как у st2_PTV_mod2.m)."""
    files.save_mat(st2_dir / "ptv_unfiltered.mat", p_arr=files.to_cell(tracks))


def draw_tracks(ax: Axes, tracks: list[np.ndarray], min_points: int) -> None:
    """Траектории на оси ax (координаты кадра, px; ось y вниз):
    синие — длиннее min_points точек, оранжевые — короткие (2…min_points точек),
    красные точки — капли, которые трекинг ни с чем не связал (траектории из одной точки)."""
    long_tracks = [t[:, 3:5] for t in tracks if t.shape[0] > min_points]
    short_tracks = [t[:, 3:5] for t in tracks if 2 <= t.shape[0] <= min_points]
    alone = np.array([t[0, 3:5] for t in tracks if t.shape[0] == 1]).reshape(-1, 2)

    ax.plot(alone[:, 0], alone[:, 1], ".", color="red", markersize=4)
    for group, color, width in ((short_tracks, "tab:orange", 0.7), (long_tracks, "tab:blue", 1.0)):
        if group:
            ax.add_collection(LineCollection(group, linewidths=width, colors=color))
            points = np.vstack(group)
            ax.plot(points[:, 0], points[:, 1], ".", markersize=3, color=color)
    ax.legend(handles=[
        Line2D([], [], color="tab:blue", marker=".", label=f"траектории длиннее {min_points} точек: {len(long_tracks)}"),
        Line2D([], [], color="tab:orange", marker=".", label=f"короткие, 2–{min_points} точек: {len(short_tracks)}"),
        Line2D([], [], color="red", marker=".", linestyle="", label=f"капли без траектории: {len(alone)}"),
    ], loc="upper left")
    ax.set_aspect("equal")
    ax.invert_yaxis()
    ax.autoscale_view()


def plot_tracks(path: Path, tracks: list[np.ndarray], min_points: int) -> None:
    """tracks.png: draw_tracks в файл."""
    fig = Figure(figsize=(19.2, 10.84))
    draw_tracks(fig.add_subplot(), tracks, min_points)
    fig.savefig(path, dpi=100)


# ------------------------------------------------------------------ полином как в MATLAB


class PolyFit:
    """[p, S] = polyfit(x, y, n) и [y, delta] = polyval(p, x, S) из MATLAB.

    Считается тем же способом, что в MATLAB (QR-разложение, схема Горнера): для неподвижных
    капель трекинг сравнивает величины порядка 1e-26, и шум округления меняет решения.
    """

    def __init__(self, x: np.ndarray, y: np.ndarray, degree: int) -> None:
        x, y = np.asarray(x, dtype=np.float64).ravel(), np.asarray(y, dtype=np.float64).ravel()
        v = np.ones((x.size, degree + 1))  # матрица Вандермонда [x^n ... x 1]
        for j in range(degree - 1, -1, -1):
            v[:, j] = x * v[:, j + 1]
        q, self.r = np.linalg.qr(v)
        if degree == 1 and np.all(y == y[0]):
            # неподвижная капля: MATLAB даёт точную константу, numpy — шум ~1e-13
            self.coeffs = np.zeros(degree + 1)
            self.coeffs[-1] = y[0]
        else:
            self.coeffs = solve_triangular(self.r, q.T @ y, lower=False)
        self.normr = float(np.linalg.norm(y - v @ self.coeffs))
        self.df = max(0, y.size - (degree + 1))  # степени свободы

    def value_and_delta(self, x: float) -> tuple[float, float]:
        """Значение полинома в x и оценка его погрешности (delta из polyval)."""
        p = self.coeffs
        n = p.size - 1
        y = p[0]
        for k in range(1, p.size):
            y = x * y + p[k]
        if self.df == 0:
            return float(y), float("inf")
        a = np.array([x ** (n - k) for k in range(n + 1)], dtype=np.float64)
        e = solve_triangular(self.r, a, trans="T", lower=False)
        return float(y), float(np.sqrt(1.0 + np.sum(e * e)) * self.normr / np.sqrt(self.df))


# ------------------------------------------------------------------ трекинг


def _dist2(point: np.ndarray, points: np.ndarray) -> np.ndarray:
    """Квадраты расстояний от точки (2,) до точек (n, 2)."""
    return (point[0] - points[:, 0]) ** 2 + (point[1] - points[:, 1]) ** 2


class Tracker:
    """Трекинг назад по времени; параметры — в thresholds.py (раздел st2).

    search_radius — радиус поиска капли около предсказания, px (PTV_SEARCH_RADIUS);
    max_radius_ratio — допустимое отношение радиусов соседних точек; max_frame_gap — наибольший пропуск,
    кадров; max_deviation — порог отклонения от полинома; min_straightness — порог прямолинейности;
    piv_weight — вес PIV в предсказании; poly_points — по скольким последним точкам строится полином.
    """

    def __init__(self, search_radius: float, max_radius_ratio: float, max_frame_gap: int,
                 max_deviation: float, min_straightness: float, piv_weight: float,
                 poly_points: int) -> None:
        self.search_radius = search_radius
        self.max_radius_ratio = max_radius_ratio
        self.max_frame_gap = max_frame_gap
        self.max_deviation = max_deviation
        self.min_straightness = min_straightness
        self.piv_weight = piv_weight
        self.poly_points = poly_points

    def track(self, centers: list[np.ndarray], radii: list[np.ndarray],
              xd: list[np.ndarray], yd: list[np.ndarray]) -> list[np.ndarray]:
        """Траектории по каплям всех кадров (списки по кадрам, как из files.load_particles)."""

        def new_track(frame: int, n: int) -> np.ndarray:  # frame, n — с 1
            c = centers[frame - 1]
            k = n - 1
            return np.array([[frame, n, 0.0, c[k, 0], c[k, 1], radii[frame - 1][k],
                              xd[frame - 1][k], yd[frame - 1][k]] + [NAN] * 8])

        # ВОПРОС К СПЕЦИАЛИСТУ (Q3, см. QUESTIONS.md). Как в MATLAB: траектории начинаются на предпоследнем
        # кадре N-1, цикл идёт с кадра N-3, так что кадр N-2 и последний кадр N не используются.
        last_frame = len(centers) - 1
        tracks = [new_track(last_frame, n) for n in range(1, centers[last_frame - 1].shape[0] + 1)]

        for i in range(last_frame - 2, 0, -1):
            if centers[i - 1].shape[0] == 0:
                continue
            taken_by = np.zeros(centers[i - 1].shape[0], dtype=np.int64)  # номер траектории (с 1), 0 — свободна
            for t in range(len(tracks)):  # только траектории, существовавшие до этого кадра
                self._extend(tracks, t, i, centers[i - 1], radii[i - 1], xd[i - 1], yd[i - 1], taken_by)
            for n0 in np.flatnonzero(taken_by == 0):
                tracks.append(new_track(i, int(n0) + 1))
        return tracks

    def _extend(self, tracks: list[np.ndarray], t: int, i: int, centers: np.ndarray, radii: np.ndarray,
                xd: np.ndarray, yd: np.ndarray, taken_by: np.ndarray) -> None:
        """Пытается продлить траекторию t каплей кадра i."""
        track = tracks[t]
        last = track[-1]
        frames_back = last[0] - i
        if not frames_back <= self.max_frame_gap:
            return
        npts = track.shape[0]

        # предсказание по PIV
        predicted_piv = np.array([last[3] - last[6] * frames_back, last[4] - last[7] * frames_back])

        # предсказание по полиному
        if npts >= 3:
            degree = 1 if npts == 3 else 2
            recent = track[max(0, npts - self.poly_points) :]
            px, dx = PolyFit(recent[:, 0], recent[:, 3], degree).value_and_delta(i)
            py, dy = PolyFit(recent[:, 0], recent[:, 4], degree).value_and_delta(i)
            predicted_poly = np.array([px, py])
            # ВОПРОС К СПЕЦИАЛИСТУ (Q5, см. QUESTIONS.md). Как в MATLAB: abs(dx + i*dy), где i — номер кадра,
            # а не мнимая единица (переменная цикла затёрла её). Вероятно, задумано sqrt(dx² + dy²).
            poly_delta = abs(dx + i * dy) if npts > 3 else NAN
        else:
            predicted_poly = np.array([NAN, NAN])
            poly_delta = NAN

        has_poly = not np.any(np.isnan(predicted_poly))
        w = self.piv_weight
        predicted = w * predicted_piv + (1 - w) * predicted_poly if has_poly else predicted_piv

        # радиус поиска: сужается до погрешности полинома, но не меньше половины исходного
        if math.isnan(poly_delta):
            radius = self.search_radius
        else:
            radius = max(self.search_radius / 2, min(self.search_radius, poly_delta))

        # ближайшая к предсказанию капля (NaN не учитываются; все NaN -> первая)
        d2_all = _dist2(predicted, centers)
        n = 0 if np.all(np.isnan(d2_all)) else int(np.nanargmin(d2_all))
        d2 = float(d2_all[n])

        # отклонение от предсказания полинома: (расстояние от предсказания до капли)² /
        # (расстояние от предсказания до последней точки)² не больше max_deviation.
        # ВОПРОС К СПЕЦИАЛИСТУ (Q6, см. QUESTIONS.md). Как в MATLAB: если предсказание совпало с последней
        # точкой и капля точно на месте, получается 0/0 = NaN, и проверка проходит.
        if has_poly:
            off = float(_dist2(predicted_poly, centers[n : n + 1])[0])
            step = float(_dist2(predicted_poly, last[None, 3:5])[0])
            with np.errstate(divide="ignore", invalid="ignore"):
                if np.float64(off) / np.float64(step) > self.max_deviation:
                    return

        # прямолинейность: |p₋₁ n| / (|p₋₁ p| + |p n|) не меньше min_straightness, где p₋₁ —
        # предпоследняя точка, p — последняя, n — капля; 1 — точки на одной прямой (0/0 — как в Q6).
        if npts >= 2:
            prev_xy, last_xy = track[-2, 3:5], last[3:5]
            a = math.sqrt(float(_dist2(prev_xy, centers[n : n + 1])[0]))
            b = math.sqrt(float(_dist2(prev_xy, last_xy[None, :])[0]))
            c = math.sqrt(float(_dist2(last_xy, centers[n : n + 1])[0]))
            with np.errstate(divide="ignore", invalid="ignore"):
                if np.float64(a) / (np.float64(b) + np.float64(c)) < self.min_straightness:
                    return

        r_ratio = radii[n] / last[5]
        if not (d2 < radius**2 and 1 / self.max_radius_ratio < r_ratio < self.max_radius_ratio):
            return

        if taken_by[n] != 0:  # каплю уже взяла другая траектория — её последняя точка
            other_t = int(taken_by[n]) - 1
            other = tracks[other_t]
            # Задумано: капля достаётся той траектории, чьё предсказание точнее (меньше радиус поиска;
            # при равных — ближе к предсказанию), и только если наша траектория длиннее.
            # ВОПРОС К СПЕЦИАЛИСТУ (Q4, см. QUESTIONS.md). Как в MATLAB: taken_point_p(16) и taken_point_p(3) —
            # линейная индексация по столбцам матрицы k×16, т. е. не радиус поиска и расстояние
            # последней точки (other[-1, 15], other[-1, 2]), а случайные элементы; к тому же расстояние
            # сравнивается с квадратом расстояния d2.
            other_radius = float(other.ravel(order="F")[15])
            other_distance = float(other.ravel(order="F")[2])
            other_loses = other_radius > radius or (other_radius == radius and other_distance > d2)
            if not (other_loses and npts > other.shape[0]):
                return
            tracks[other_t] = other[:-1, :]

        row = np.concatenate([[i, n + 1, math.sqrt(d2)], centers[n], [radii[n], xd[n], yd[n]],
                              predicted_piv, predicted_poly, predicted, [poly_delta, radius]])
        tracks[t] = np.vstack([track, row])
        taken_by[n] = t + 1
