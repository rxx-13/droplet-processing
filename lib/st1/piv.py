"""st1: PIV — смещения между двумя кадрами по взаимной корреляции окон (порт myf_DoCC8_multimax.m)
и фильтрация результата (порт Filter_PIV_output.m).

Сетка. Узлы через step пикселей от 0 до размера кадра; вокруг узла — квадратное окно window×window.
Окна, не помещающиеся в кадр, не считаются (status = 1). Массивы результата имеют форму (nx, ny):
ось 0 — x, ось 1 — y (так их хранит MATLAB).

Корреляция считается через FFT. Ищутся все локальные максимумы корреляции; смещение — по
наибольшему, с субпиксельным уточнением по трём точкам (гауссова аппроксимация).
Качество вектора — отношение наибольшего максимума ко второму (CC_maxRaitio).

status: 0 — вектор найден; 1 — окно не помещается в кадр; 4 — максимум на краю окна;
41 — субпиксельное уточнение невозможно; 77 — мало отношение пиков; 78 — смещение больше max_displ.
"""

import time
import warnings
from dataclasses import dataclass, replace
from types import ModuleType
from typing import Any

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from .image_ops import regional_max


@dataclass(frozen=True)
class PivGrid:
    """Узлы PIV: x_arr — по горизонтали, y_arr — по вертикали, px (аналог класса PIV_Grid в MATLAB).
    Массивы по узлам имеют форму (nx, ny): ось 0 — x, ось 1 — y."""

    x_arr: np.ndarray
    y_arr: np.ndarray

    @classmethod
    def regular(cls, width: int, height: int, step: int) -> "PivGrid":
        """Узлы 0, step, 2·step, … до ширины и высоты кадра (PIV_Grid(0:step:W, 0:step:H))."""
        return cls(np.arange(0, width + 1, step, dtype=np.float64),
                   np.arange(0, height + 1, step, dtype=np.float64))

    @property
    def shape(self) -> tuple[int, int]:
        """(nx, ny) — число узлов по x и по y."""
        return self.x_arr.size, self.y_arr.size

    @property
    def x_mat(self) -> np.ndarray:
        """x каждого узла, форма (nx, ny)."""
        return np.repeat(self.x_arr[:, None], self.y_arr.size, axis=1)

    @property
    def y_mat(self) -> np.ndarray:
        """y каждого узла, форма (nx, ny)."""
        return np.repeat(self.y_arr[None, :], self.x_arr.size, axis=0)

    def to_struct(self) -> dict[str, Any]:
        """Поля объекта PIV_Grid из MATLAB."""

        def step(arr: np.ndarray) -> float:
            d = np.diff(arr)
            return float(d[0]) if arr.size > 1 and np.all(d == d[0]) else float("nan")

        return {"xMat": self.x_mat, "yMat": self.y_mat,
                "xArr": self.x_arr.reshape(1, -1), "yArr": self.y_arr.reshape(1, -1),
                "xStep": step(self.x_arr), "yStep": step(self.y_arr)}


@dataclass(frozen=True)
class PivResult:
    """PIV для пары кадров, массивы формы (nx, ny). Смещения xdispl, ydispl — px за кадр
    (y вниз); status — код результата (0 — вектор найден, см. описание модуля); cc_max_value —
    высота главного пика корреляции; cc_max_ratio — отношение главного пика ко второму."""

    status: np.ndarray
    xdispl: np.ndarray
    ydispl: np.ndarray
    cc_max_value: np.ndarray
    cc_max_ratio: np.ndarray
    mean_im1: np.ndarray  # средняя яркость окна первого кадра
    grid: PivGrid
    params: dict[str, Any]  # PIV_params в формате MATLAB (записывается в .mat)
    image_size: tuple[int, int]  # (ширина, высота)
    time_of_iteration: float

    def to_struct(self) -> dict[str, Any]:
        """Структура PIV_output, как её сохраняет MATLAB."""
        return {
            "status": self.status, "xdispl": self.xdispl, "ydispl": self.ydispl,
            "CC_maxValue": self.cc_max_value, "CC_maxRaitio": self.cc_max_ratio,
            "CC_infoArr": np.zeros((0, 0)), "PIV_params": self.params,
            "imSize": np.array([self.image_size], dtype=np.float64),
            "mean_im1": self.mean_im1, "time_of_iteration": float(self.time_of_iteration),
        }


class Piv:
    """PIV на регулярной сетке с окном window×window и шагом step (порт myf_DoCC8_multimax.m).

    width, height — размер кадра, px; max_displ — наибольшее искомое смещение, px; min_peak_ratio —
    наименьшее отношение 1-го пика корреляции ко 2-му; min_displ_for_output — только записывается
    в PIV_params.minDispl (алгоритмом не используется).
    """

    def __init__(self, width: int, height: int, window: int, step: int,
                 max_displ: int, min_peak_ratio: float, min_displ_for_output: float) -> None:
        self.window = window
        self.max_displ = max_displ  # корреляция дальше этого смещения не рассматривается, px
        self.min_peak_ratio = min_peak_ratio  # ниже — вектор отбраковывается (status 77)
        self.grid = PivGrid.regular(width, height, step)
        self.image_size = (width, height)
        self.params = {  # PIV_params в .mat (minDispl алгоритмом не используется, только сохраняется)
            "wSize": np.array([[window, window]], dtype=np.float64),
            "maxDispl": np.array([[max_displ, max_displ]], dtype=np.float64),
            "minDispl": np.array([[min_displ_for_output, min_displ_for_output]], dtype=np.float64),
            "minS2NRatio": float(min_peak_ratio),
            "useSubpixel": np.bool_(True), "useBiasCorrection": np.bool_(False),
            "saveCCandIMC": np.bool_(False), "doMultiMax": np.bool_(True),
            "piv_grid": self.grid.to_struct(),
            "xIDArr": 0.0, "yIDArr": 0.0, "doCCArr": np.zeros((0, 0)),
        }

    @classmethod
    def from_thresholds(cls, t: ModuleType, width: int, height: int) -> "Piv":
        """Из раздела «PIV» thresholds.py эксперимента для кадра width×height."""
        return cls(width, height, t.PIV_WINDOW, t.PIV_STEP, t.PIV_MAX_DISPLACEMENT, t.PIV_MIN_PEAK_RATIO,
                   min_displ_for_output=t.PIV_FILTER_MIN_DISPLACEMENT)

    def compute(self, im1: np.ndarray, im2: np.ndarray) -> PivResult:
        """PIV между кадрами im1 и im2 (одинакового размера; в st1 это 1 − кадр без рима, капли светлые):
        смещения из im1 в im2 в каждом узле сетки, px/кадр."""
        start = time.perf_counter()
        ws, half = self.window, self.window // 2
        img_h, img_w = im1.shape
        nx, ny = self.grid.shape
        status, xdispl, ydispl, cc_value, cc_ratio, mean_im1 = (np.full((nx, ny), np.nan) for _ in range(6))

        # окна, целиком помещающиеся в кадр (границы с 1, как в MATLAB)
        windows: list[tuple[int, int, int, int]] = []  # (xi, yi, первая строка, первый столбец)
        for xi, xc in enumerate(self.grid.x_arr):
            x_first, x_last = int(round(xc - half)), int(round(xc + half - 1))
            for yi, yc in enumerate(self.grid.y_arr):
                y_first, y_last = int(round(yc - half)), int(round(yc + half - 1))
                if x_first >= 1 and x_last <= img_w and y_first >= 1 and y_last <= img_h:
                    windows.append((xi, yi, y_first - 1, x_first - 1))
                else:
                    status[xi, yi] = 1

        if windows:
            a1 = np.stack([im1[r : r + ws, c : c + ws] for _, _, r, c in windows])
            a2 = np.stack([im2[r : r + ws, c : c + ws] for _, _, r, c in windows])
            corr = np.fft.fftshift(np.real(np.fft.ifft2(np.conj(np.fft.fft2(a1)) * np.fft.fft2(a2))),
                                   axes=(-2, -1))
            means = a1.mean(axis=(1, 2))
            for k, (xi, yi, _, _) in enumerate(windows):
                mean_im1[xi, yi] = means[k]
                (status[xi, yi], xdispl[xi, yi], ydispl[xi, yi],
                 cc_value[xi, yi], cc_ratio[xi, yi]) = self._window_displacement(corr[k])

        return PivResult(status, xdispl, ydispl, cc_value, cc_ratio, mean_im1, self.grid,
                         self.params, self.image_size, time.perf_counter() - start)

    def _window_displacement(self, corr: np.ndarray) -> tuple[float, float, float, float, float]:
        """(status, dx, dy, высота пика, отношение пиков) по корреляции одного окна."""
        ws, half, md = self.window, self.window // 2, self.max_displ
        # корреляцию для смещений не меньше max_displ обнуляем
        corr[: half + 1 - md, :] = 0
        corr[half + md :, :] = 0
        corr[:, : half + 1 - md] = 0
        corr[:, half + md :] = 0

        # все локальные максимумы (в порядке обхода по столбцам, как find в MATLAB)
        peaks = np.flatnonzero(regional_max(corr).ravel(order="F"))
        values = corr.ravel(order="F")[peaks]
        best = int(np.argmax(values))
        peak = float(values[best])
        # Отношение наибольшего максимума ко второму; один максимум -> Inf, все нули -> NaN.
        # (В MATLAB ветка «один максимум» недостижима из-за `numel(maximums_v > 1)`,
        # результат тот же.)
        rest = values.copy()
        rest[best] = 0.0
        with np.errstate(divide="ignore", invalid="ignore"):
            ratio = float(np.float64(peak) / np.float64(np.max(rest)))

        location = int(peaks[best])
        yp, xp = location % ws, location // ws  # строка и столбец максимума
        dx, dy = xp - half, yp - half
        nan = float("nan")
        if ratio < self.min_peak_ratio:
            return 77, nan, nan, peak, ratio
        if not (abs(dx) < md and abs(dy) < md):
            return 78, nan, nan, peak, ratio
        if not (1 <= xp <= ws - 2 and 1 <= yp <= ws - 2):
            return 4, 0 + dx, 0 + dy, peak, ratio

        # субпиксельное уточнение: гауссиана по максимуму и двум соседям
        cx = corr[yp, xp - 1 : xp + 2]
        cy = corr[yp - 1 : yp + 2, xp]
        with np.errstate(divide="ignore", invalid="ignore"):
            cx23, cx12 = cx[1] / cx[2], cx[0] / cx[1]
            cy23, cy12 = cy[1] / cy[2], cy[0] / cy[1]
            ratios = np.array([cx12, cx23, cy12, cy23])
            if np.any(ratios > 1e10) or np.any(ratios <= 0):
                return 41, 0 + dx, 0 + dy, peak, ratio
            x_sub = -0.5 * (np.log(cx23) + np.log(cx12)) / (np.log(cx23) - np.log(cx12))
            y_sub = -0.5 * (np.log(cy23) + np.log(cy12)) / (np.log(cy23) - np.log(cy12))
        if not (abs(x_sub) < 1 and abs(y_sub) < 1):
            raise RuntimeError("PIV: субпиксельное положение максимума вне ±1 px")
        return 0, dx + x_sub, dy + y_sub, peak, ratio


def median_ignoring_nan(a: np.ndarray, size: int) -> np.ndarray:
    """Медиана по окну size×size без учёта NaN (mediannan.m); где было NaN — остаётся NaN."""
    m = (size - 1) // 2
    padded = np.full((a.shape[0] + 2 * m, a.shape[1] + 2 * m), np.nan)
    padded[m : m + a.shape[0], m : m + a.shape[1]] = a
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # окно из одних NaN -> NaN
        med = np.nanmedian(sliding_window_view(padded, (size, size)), axis=(-2, -1))
    med[np.isnan(a)] = np.nan
    return med


def filter_piv(piv: PivResult, min_peak_ratio: float, min_displ: float, median_size: int) -> PivResult:
    """Отбраковка векторов и медианное сглаживание (Filter_PIV_output.m).

    Задумано: вектор остаётся, если status = 0, отношение пиков > min_peak_ratio и смещение
    больше min_displ.
    """
    with np.errstate(invalid="ignore"):
        # ВОПРОС К СПЕЦИАЛИСТУ (Q1, см. QUESTIONS.md). Как в MATLAB: `&` выполняется раньше `|`, поэтому
        # при |yd| > min_displ status и отношение пиков НЕ проверяются. Вероятно, задумано
        # (status == 0) & (ratio > min) & ((|xd| > d) | (|yd| > d)).
        good = ((piv.status == 0) & (piv.cc_max_ratio > min_peak_ratio) & (np.abs(piv.xdispl) > min_displ)) | (
            np.abs(piv.ydispl) > min_displ
        )
    xd = median_ignoring_nan(np.where(good, piv.xdispl, np.nan), median_size)
    yd = median_ignoring_nan(np.where(good, piv.ydispl, np.nan), median_size)
    return replace(piv, xdispl=xd, ydispl=yd)


def filter_piv_by_thresholds(piv: PivResult, t: ModuleType) -> PivResult:
    """filter_piv с параметрами из раздела «PIV» thresholds.py эксперимента."""
    return filter_piv(piv, t.PIV_FILTER_MIN_PEAK_RATIO, t.PIV_FILTER_MIN_DISPLACEMENT, t.PIV_FILTER_MEDIAN_SIZE)
