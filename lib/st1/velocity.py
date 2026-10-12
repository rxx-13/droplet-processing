"""st1: скорость PIV в центрах капель.

Повторяет scatteredInterpolant(x, y, v, 'linear', 'nearest') из MATLAB: внутри области, покрытой
узлами PIV, — линейная интерполяция по треугольникам Делоне; снаружи — значение ближайшего узла.
Если валидных узлов меньше трёх, скорость не определена (NaN).

Почему своя триангуляция. На регулярной сетке PIV каждые 4 соседних узла лежат на одной
окружности, и любая из двух диагоналей квадрата одинаково «правильная». MATLAB выбирает её по
порядку вставки точек, scipy (Qhull) — иначе, и интерполированные скорости части капель
различаются. Здесь точки вставляются по одной в порядке (x, затем y), и при «ничьей» остаётся
уже построенное ребро — как в MATLAB.
"""

import logging
from fractions import Fraction

import numpy as np
from scipy.spatial import cKDTree

from .piv import PivResult

log = logging.getLogger(__name__)


def velocity_at_points(centers: np.ndarray, piv: PivResult) -> tuple[np.ndarray, np.ndarray]:
    """Смещения PIV (px/кадр) в точках centers (N, 2: x, y; px с 1)."""
    valid = ~np.isnan(piv.xdispl) & ~np.isnan(piv.ydispl)
    x, y = piv.grid.x_mat[valid], piv.grid.y_mat[valid]
    interpolate = Interpolator(x, y)
    return interpolate(piv.xdispl[valid], centers), interpolate(piv.ydispl[valid], centers)


class Interpolator:
    """Линейная интерполяция по триангуляции узлов (x, y), снаружи — ближайший узел
    (scatteredInterpolant(x, y, v, 'linear', 'nearest') в MATLAB). Меньше 3 узлов — результат NaN."""

    def __init__(self, x: np.ndarray, y: np.ndarray) -> None:
        x, y = np.ravel(x).astype(np.float64), np.ravel(y).astype(np.float64)
        self.order = np.lexsort((y, x))  # порядок вставки: по x, затем по y
        self.points = np.column_stack([x[self.order], y[self.order]])
        self.ok = len(self.points) >= 3
        if not self.ok:
            log.warning("Интерполяция скорости: валидных узлов PIV %d (< 3), скорость = NaN",
                        len(self.points))
            return
        self.tree = cKDTree(self.points)
        self.triangulation = Delaunay(self.points)

    def __call__(self, values: np.ndarray, query: np.ndarray) -> np.ndarray:
        """values — значения в узлах (в исходном порядке); query — точки (N, 2)."""
        q = np.asarray(query, dtype=np.float64).reshape(-1, 2)
        out = np.full(len(q), np.nan)
        if not self.ok:
            return out
        v = np.ravel(values).astype(np.float64)[self.order]
        tri, w = self.triangulation.find(q)
        inside = tri >= 0
        if inside.any():
            t = self.triangulation.simplices[tri[inside]]
            out[inside] = w[inside, 0] * v[t[:, 0]] + w[inside, 1] * v[t[:, 1]] + w[inside, 2] * v[t[:, 2]]
        outside = np.isnan(out)
        if outside.any():
            _, nearest = self.tree.query(q[outside])
            out[outside] = v[nearest]
        return out


# Окружности считаются в float64; случаи «почти на окружности» (относительная разница
# меньше этой величины) пересчитываются точно, в рациональных числах.
_ON_CIRCLE_TOLERANCE = 1e-9


def _exact(v: float) -> int | Fraction:
    """Число float64 точно, без округления: целое или рациональная дробь (для точной проверки окружности)."""
    f = float(v)
    return int(f) if f.is_integer() else Fraction(f)


def _incircle_exact(a: tuple, b: tuple, c: tuple, p: tuple) -> int | Fraction:
    """> 0: p строго внутри окружности треугольника abc (обход против часовой); 0: на окружности."""
    ax, ay = a[0] - p[0], a[1] - p[1]
    bx, by = b[0] - p[0], b[1] - p[1]
    cx, cy = c[0] - p[0], c[1] - p[1]
    return ((ax * ax + ay * ay) * (bx * cy - cx * by)
            - (bx * bx + by * by) * (ax * cy - cx * ay)
            + (cx * cx + cy * cy) * (ax * by - bx * ay))


class Delaunay:
    """Триангуляция Делоне с вставкой точек по одной в заданном порядке (алгоритм Боуэра–Ватсона).

    Точка ломает треугольник, только если лежит СТРОГО внутри его описанной окружности —
    поэтому при «ничьих» сохраняются уже построенные рёбра.
    """

    def __init__(self, points: np.ndarray) -> None:
        pts = np.asarray(points, dtype=np.float64).reshape(-1, 2)
        n = len(pts)
        # большой внешний треугольник, содержащий все точки
        span = float(max(np.ptp(pts[:, 0]), np.ptp(pts[:, 1]), 1.0))
        big = 1e6 * span
        cx0, cy0 = float(pts[:, 0].mean()), float(pts[:, 1].mean())
        self.x = np.concatenate([pts[:, 0], [cx0 - big, cx0 + big, cx0]])
        self.y = np.concatenate([pts[:, 1], [cy0 - big, cy0 - big, cy0 + big]])
        self._ex = [_exact(v) for v in self.x]
        self._ey = [_exact(v) for v in self.y]

        capacity = 4 * n + 16
        self._vertices = np.zeros((capacity, 3), dtype=np.int64)
        self._center_x = np.zeros(capacity)
        self._center_y = np.zeros(capacity)
        self._radius2 = np.zeros(capacity)
        self._alive = np.zeros(capacity, dtype=bool)
        self._count = 0
        self._add(n, n + 1, n + 2)
        for i in range(n):
            self._insert(i)
        alive = self._vertices[: self._count][self._alive[: self._count]]
        self.simplices = alive[(alive < n).all(axis=1)]  # без треугольников с внешними вершинами

    def _add(self, a: int, b: int, c: int) -> None:
        """Добавить треугольник с вершинами a, b, c (номера точек) и запомнить его описанную окружность."""
        if self._count == len(self._vertices):
            grow = len(self._vertices)
            self._vertices = np.vstack([self._vertices, np.zeros((grow, 3), dtype=np.int64)])
            self._center_x = np.concatenate([self._center_x, np.zeros(grow)])
            self._center_y = np.concatenate([self._center_y, np.zeros(grow)])
            self._radius2 = np.concatenate([self._radius2, np.zeros(grow)])
            self._alive = np.concatenate([self._alive, np.zeros(grow, dtype=bool)])
        x, y = self.x, self.y
        ax, ay, bx, by, cx, cy = x[a], y[a], x[b], y[b], x[c], y[c]
        d = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
        ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay)
              + (cx * cx + cy * cy) * (ay - by)) / d
        uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx)
              + (cx * cx + cy * cy) * (bx - ax)) / d
        k = self._count
        self._vertices[k] = (a, b, c)
        self._center_x[k], self._center_y[k] = ux, uy
        self._radius2[k] = (ax - ux) ** 2 + (ay - uy) ** 2
        self._alive[k] = True
        self._count += 1

    def _insert(self, i: int) -> None:
        """Вставить точку i: убрать треугольники, в описанную окружность которых она попала строго
        внутрь, и соединить точку с границей образовавшейся «дыры»."""
        m = self._count
        px, py = self.x[i], self.y[i]
        alive = self._alive[:m]
        d2 = (px - self._center_x[:m]) ** 2 + (py - self._center_y[:m]) ** 2
        r2 = self._radius2[:m]
        tolerance = _ON_CIRCLE_TOLERANCE * np.maximum(r2, 1.0)
        broken = alive & (d2 < r2 - tolerance)
        for t in np.flatnonzero(alive & (np.abs(d2 - r2) <= tolerance)):  # спорные — точно
            a, b, c = self._vertices[t]
            broken[t] = _incircle_exact((self._ex[a], self._ey[a]), (self._ex[b], self._ey[b]),
                                        (self._ex[c], self._ey[c]), (self._ex[i], self._ey[i])) > 0
        broken_idx = np.flatnonzero(broken)
        edges: set[tuple[int, int]] = set()
        for t in broken_idx:
            a, b, c = (int(v) for v in self._vertices[t])
            edges.update(((a, b), (b, c), (c, a)))
        boundary = [e for e in edges if (e[1], e[0]) not in edges]  # граница «дыры»
        self._alive[broken_idx] = False
        for a, b in boundary:
            self._add(a, b, i)

    def find(self, q: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Для точек q (m, 2): номер содержащего треугольника в simplices (или -1)
        и барицентрические веса (m, 3)."""
        s = self.simplices
        m = len(q)
        if len(s) == 0:
            return np.full(m, -1), np.zeros((m, 3))
        a, b, c = (np.column_stack([self.x[s[:, k]], self.y[s[:, k]]]) for k in range(3))
        det = (b[:, 1] - c[:, 1]) * (a[:, 0] - c[:, 0]) + (c[:, 0] - b[:, 0]) * (a[:, 1] - c[:, 1])
        qx, qy = q[:, 0:1], q[:, 1:2]
        l1 = ((b[:, 1] - c[:, 1]) * (qx - c[:, 0]) + (c[:, 0] - b[:, 0]) * (qy - c[:, 1])) / det
        l2 = ((c[:, 1] - a[:, 1]) * (qx - c[:, 0]) + (a[:, 0] - c[:, 0]) * (qy - c[:, 1])) / det
        l3 = 1.0 - l1 - l2
        inside = (l1 >= -1e-12) & (l2 >= -1e-12) & (l3 >= -1e-12)
        tri = np.where(inside.any(axis=1), inside.argmax(axis=1), -1)
        rows = np.arange(m)
        w = np.zeros((m, 3))
        ok = tri >= 0
        w[ok, 0], w[ok, 1], w[ok, 2] = l1[rows[ok], tri[ok]], l2[rows[ok], tri[ok]], l3[rows[ok], tri[ok]]
        return tri, w
