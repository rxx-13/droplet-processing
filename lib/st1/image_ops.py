"""Операции над изображениями для st1 — так, как их выполняет MATLAB Image Processing Toolbox.

Стандартные функции scipy/scikit-image в мелочах ведут себя иначе (форма «диска», края кадра,
порядок нумерации объектов, нормировка canny), а от этих мелочей зависит, какие капли найдутся.
Поэтому здесь они повторяют MATLAB:

    disk(R)                 strel('disk', R) — структурный элемент
    dilate / erode          imdilate / imerode
    close_ / open_          imclose / imopen
    fill_holes              imfill(BW, 'holes')
    fill_holes_gray         imfill(I, 'holes') для полутонового изображения
    remove_small            bwareaopen
    label                   bwlabel (нумерация объектов по столбцам, как в MATLAB)
    centroids_and_diameters regionprops(..., 'Centroid', 'EquivDiameter')
    areas                   regionprops(..., 'Area')
    regional_max            imregionalmax
    canny                   edge(I, 'canny', T)

Координаты в результатах — в пикселях, с 1 (как в MATLAB).
"""

import math

import numpy as np
from scipy import ndimage as ndi
from skimage import measure
from skimage import morphology as skm

# ------------------------------------------------------------------ морфология

def _minkowski_sum(shape: np.ndarray, offsets: list[tuple[int, int]]) -> np.ndarray:
    """Все точки shape, сдвинутые на каждое смещение (dy, dx) из offsets (imdilate 'full')."""
    ry = max(abs(dy) for dy, _ in offsets)
    rx = max(abs(dx) for _, dx in offsets)
    out = np.zeros((shape.shape[0] + 2 * ry, shape.shape[1] + 2 * rx), dtype=bool)
    for dy, dx in offsets:
        out[ry + dy : ry + dy + shape.shape[0], rx + dx : rx + dx + shape.shape[1]] |= shape
    return out


def disk(radius: int) -> np.ndarray:
    """strel('disk', R) MATLAB (по умолчанию N = 4).

    При R < 3 — круг (R = 1 — крест 3×3). При R >= 3 MATLAB строит не круг, а восьмиугольник:
    сумму четырёх «периодических линий» (направления 0°, 45°, 90°, 135°), а если он получился
    меньше R — добавляет горизонтальный и вертикальный отрезки (алгоритм strel.m: Adams 1993,
    Jones & Soille 1996). Для R = 5 это 9×9 с |x|, |y| <= 4 и |x| + |y| <= 6 — совпадает
    с сохранёнными результатами MATLAB.
    """
    r = int(radius)
    if r < 3:
        yy, xx = np.mgrid[-r : r + 1, -r : r + 1]
        return (xx**2 + yy**2) <= r**2
    theta = math.pi / 8  # π / (2N)
    k = 2 * r / (1 / math.tan(theta) + 1 / math.sin(theta))  # длина «периодических линий»
    shape = np.ones((1, 1), dtype=bool)
    for vy, vx in ((1, 0), (1, 1), (0, 1), (-1, 1)):
        p = math.floor(k / math.hypot(vy, vx))
        shape = _minkowski_sum(shape, [(i * vy, i * vx) for i in range(-p, p + 1)])
    reached = (shape.shape[0] - 1) // 2
    half_line = r - reached - 1  # отрезки длиной 2·(R − reached − 1) + 1
    if half_line >= 1:
        shape = _minkowski_sum(shape, [(0, i) for i in range(-half_line, half_line + 1)])
        shape = _minkowski_sum(shape, [(i, 0) for i in range(-half_line, half_line + 1)])
    return shape


def dilate(bw: np.ndarray, se: np.ndarray) -> np.ndarray:
    """imdilate (за краем кадра — 0)."""
    return ndi.binary_dilation(bw, structure=se, border_value=0)


def erode(bw: np.ndarray, se: np.ndarray) -> np.ndarray:
    """imerode (за краем кадра — 1: края не «съедаются»)."""
    return ndi.binary_erosion(bw, structure=se, border_value=1)


def close_(bw: np.ndarray, se: np.ndarray) -> np.ndarray:
    """imclose. Как MATLAB, сначала расширяем кадр нулями на размер элемента: иначе эрозия
    видит за краем единицы, и объекты у края кадра получаются больше."""
    r0, r1 = se.shape[0] // 2, se.shape[1] // 2
    padded = np.pad(bw, ((r0, r0), (r1, r1)))
    out = erode(dilate(padded, se), se)
    return out[r0 : out.shape[0] - r0, r1 : out.shape[1] - r1]


def open_(bw: np.ndarray, se: np.ndarray) -> np.ndarray:
    """imopen."""
    return dilate(erode(bw, se), se)


def fill_holes(bw: np.ndarray) -> np.ndarray:
    """imfill(BW, 'holes')."""
    return ndi.binary_fill_holes(bw)


def fill_holes_gray(img: np.ndarray) -> np.ndarray:
    """imfill(I, 'holes') для полутонового изображения: тёмные области, не касающиеся края кадра,
    поднимаются до уровня окружения (реконструкция эрозией, 4-связность)."""
    seed = img.copy()
    seed[1:-1, 1:-1] = img.max()
    return skm.reconstruction(seed, img, method="erosion", footprint=ndi.generate_binary_structure(2, 1))


def remove_small(bw: np.ndarray, min_pixels: int) -> np.ndarray:
    """bwareaopen: убирает объекты (8-связность) меньше min_pixels пикселей."""
    lab = measure.label(bw.astype(bool), connectivity=2)
    keep = np.bincount(lab.ravel()) >= min_pixels
    keep[0] = False
    return keep[lab]


# ------------------------------------------------------------------ объекты


def label(bw: np.ndarray, connectivity: int = 8) -> tuple[np.ndarray, int]:
    """bwlabel: метки объектов и их число. Нумерация — по первому пикселю при обходе по столбцам,
    как в MATLAB (skimage нумерует по строкам). От порядка капель зависит трекинг в st2."""
    lab = measure.label(bw.astype(bool), connectivity=2 if connectivity == 8 else 1)
    n = int(lab.max())
    if n == 0:
        return lab, 0
    flat = lab.ravel(order="F")
    nonzero = np.flatnonzero(flat)
    labels, first_index = np.unique(flat[nonzero], return_index=True)
    first = np.full(n + 1, np.iinfo(np.int64).max, dtype=np.int64)
    first[labels] = nonzero[first_index]
    order = np.argsort(first[1:], kind="stable") + 1  # старые метки в новом порядке
    renumber = np.zeros(n + 1, dtype=lab.dtype)
    renumber[order] = np.arange(1, n + 1)
    return renumber[lab], n


def centroids_and_diameters(bw: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Центры (N, 2: x, y; px с 1) и эквивалентные диаметры (N,) объектов, 8-связность."""
    lab, n = label(bw, 8)
    if n == 0:
        return np.zeros((0, 2)), np.zeros(0)
    rows, cols = np.nonzero(lab)
    labels = lab[rows, cols]
    area = np.bincount(labels, minlength=n + 1)[1:].astype(np.float64)
    cx = np.bincount(labels, weights=cols + 1.0, minlength=n + 1)[1:] / area
    cy = np.bincount(labels, weights=rows + 1.0, minlength=n + 1)[1:] / area
    return np.column_stack([cx, cy]), np.sqrt(4.0 * area / np.pi)


def areas(bw: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Метки объектов (8-связность) и площади объектов 1..N в пикселях."""
    lab, n = label(bw, 8)
    return lab, np.bincount(lab.ravel(), minlength=n + 1)[1:]


def regional_max(a: np.ndarray) -> np.ndarray:
    """imregionalmax (8-связность, плато у края тоже считаются).
    На постоянном изображении MATLAB помечает все пиксели (skimage — ни одного)."""
    mask = skm.local_maxima(a, connectivity=2, allow_borders=True)
    if not mask.any() and a.size and a.min() == a.max():
        return np.ones(a.shape, dtype=bool)
    return mask


# ------------------------------------------------------------------ canny

CANNY_SIGMA = math.sqrt(2.0)  # сглаживание по умолчанию в MATLAB
CANNY_LOW_TO_HIGH = 0.4  # нижний порог = 0.4 × верхний (ThresholdRatio в MATLAB)


def _smooth_gradient(img: np.ndarray, sigma: float) -> tuple[np.ndarray, np.ndarray]:
    """Градиент сглаженного изображения: гауссиан и его численная производная (как в MATLAB)."""
    extent = math.ceil(4 * sigma)
    x = np.arange(-extent, extent + 1, dtype=np.float64)
    gauss = np.exp(-(x**2) / (2 * sigma**2)) / (math.sqrt(2 * math.pi) * sigma)
    gauss /= gauss.sum()
    deriv = np.gradient(gauss)
    pos, neg = deriv > 0, deriv < 0
    deriv[pos] /= deriv[pos].sum()
    deriv[neg] /= abs(deriv[neg].sum())
    gx = ndi.convolve1d(img, gauss, axis=0, mode="reflect")
    gx = ndi.convolve1d(gx, deriv, axis=1, mode="reflect")
    gy = ndi.convolve1d(img, gauss, axis=1, mode="reflect")
    gy = ndi.convolve1d(gy, deriv, axis=0, mode="reflect")
    return gx, gy


def _thin_edges(ix: np.ndarray, iy: np.ndarray, mag: np.ndarray) -> np.ndarray:
    """Оставляет только локальные максимумы градиента поперёк границы
    (4 сектора направлений, соседи интерполируются линейно)."""
    h, w = mag.shape
    padded = np.pad(mag, 1)

    def shifted(dy: int, dx: int) -> np.ndarray:
        """mag в точке (y + dy, x + dx) для каждой (y, x)."""
        return padded[1 + dy : 1 + dy + h, 1 + dx : 1 + dx + w]

    out = np.zeros_like(mag, dtype=bool)
    with np.errstate(divide="ignore", invalid="ignore"):
        sectors = [
            (((iy <= 0) & (ix > -iy)) | ((iy >= 0) & (ix < -iy)), np.abs(iy / ix), (0, 1), (-1, 1), (0, -1), (1, -1)),
            (((ix > 0) & (-iy >= ix)) | ((ix < 0) & (-iy <= ix)), np.abs(ix / iy), (-1, 0), (-1, 1), (1, 0), (1, -1)),
            (((ix <= 0) & (ix > iy)) | ((ix >= 0) & (ix < iy)), np.abs(ix / iy), (-1, 0), (-1, -1), (1, 0), (1, 1)),
            (((iy < 0) & (ix <= iy)) | ((iy > 0) & (ix >= iy)), np.abs(iy / ix), (0, -1), (-1, -1), (0, 1), (1, 1)),
        ]
        for in_sector, d, a1, b1, a2, b2 in sectors:
            neighbour1 = shifted(*a1) * (1 - d) + shifted(*b1) * d
            neighbour2 = shifted(*a2) * (1 - d) + shifted(*b2) * d
            out |= in_sector & (mag >= neighbour1) & (mag >= neighbour2)
    out[0, :] = out[-1, :] = False
    out[:, 0] = out[:, -1] = False
    return out


def canny(img: np.ndarray, threshold: float) -> np.ndarray:
    """edge(I, 'canny', T): карта границ. threshold — верхний порог на градиент,
    нормированный на его максимум по кадру; слабые границы сохраняются, если касаются сильных."""
    ix, iy = _smooth_gradient(img.astype(np.float64), CANNY_SIGMA)
    mag = np.hypot(ix, iy)
    if mag.max() > 0:
        mag = mag / mag.max()
    thin = _thin_edges(ix, iy, mag)
    strong = thin & (mag > threshold)
    weak = thin & (mag > CANNY_LOW_TO_HIGH * threshold)
    if not strong.any():
        return np.zeros_like(weak)
    lab, n = label(weak, 8)
    keep = np.zeros(n + 1, dtype=bool)
    keep[np.unique(lab[strong])] = True
    keep[0] = False
    return keep[lab]
