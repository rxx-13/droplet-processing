"""st1: удаление рима и крупных брызг, поиск капель на кадре.

Яркость: капли и рим тёмные на светлом фоне. После удаления рима кадр нормирован в 0..1,
где 1 — фон (и закрашенные рим, брызги, вода), меньше 1 — капли.
"""

from types import ModuleType

import numpy as np

from . import image_ops as ops


def stretch(img: np.ndarray, low: float, high: float) -> np.ndarray:
    """mat2gray(I, [low high]): low -> 0, high -> 1, всё вне диапазона обрезается."""
    return np.clip((img - low) / (high - low), 0.0, 1.0)


Region = tuple[int, int | None, int, int | None]  # строки от–до, столбцы от–до, px с 1; None — до края


class RimRemover:
    """Закрашивает фоном (1) рим, крупные брызги и водную поверхность.

    Шаги: кадр минус фон -> растяжение яркости -> вручную убранные области (erase_regions) — фоном ->
    чёрно-белое изображение -> «закрытие» -> закрашивание воды ниже surface_row -> заливка дыр ->
    объекты больше rim_area пикселей (рим и крупные брызги), расширенные на dilate_radius, закрашиваются.

    gray_range — (L, H): разность «кадр − фон» от L до H растягивается в 0..1 (RIM_GRAY_RANGE);
    binarize_threshold — порог «тёмного» после растяжения; close_radius, dilate_radius — радиусы дисков, px;
    surface_row — строка (с 1), начиная с которой вниз — вода; erase_regions — вручную закрашиваемые
    прямоугольники (ERASE_REGIONS). MATLAB: блок «удаление крупных брызг» st1_droplets_separation.m.
    """

    def __init__(self, gray_range: tuple[float, float], binarize_threshold: float,
                 close_radius: int, dilate_radius: int, surface_row: int,
                 erase_regions: list[Region] | tuple[Region, ...] = ()) -> None:
        self.gray_range = gray_range
        self.binarize_threshold = binarize_threshold
        self.close_disk = ops.disk(close_radius)
        self.dilate_disk = ops.disk(dilate_radius)
        self.surface_row = surface_row  # с 1, как в MATLAB
        self.erase_regions = tuple(erase_regions)

    @classmethod
    def from_thresholds(cls, t: ModuleType, surface_row: int,
                        erase_regions: list[Region] | tuple[Region, ...] = ()) -> "RimRemover":
        """Из раздела «удаление рима» thresholds.py; строка воды и убранные области — из settings.py."""
        return cls(t.RIM_GRAY_RANGE, t.RIM_BINARIZE_THRESHOLD, t.RIM_CLOSE_RADIUS, t.RIM_DILATE_RADIUS,
                   surface_row, erase_regions)

    def remove(self, frame: np.ndarray, background: np.ndarray, rim_area: float) -> np.ndarray:
        """Кадр без рима: 0..1, фон и закрашенное = 1. frame, background — кадры (строки × столбцы),
        уровни яркости файла; rim_area — порог площади рима, px² (колонка rim таблицы)."""
        return self.remove_with_mask(frame, background, rim_area)[0]

    def remove_with_mask(self, frame: np.ndarray, background: np.ndarray,
                         rim_area: float) -> tuple[np.ndarray, np.ndarray]:
        """Кадр без рима и маска закрашенного (рим, крупные брызги, вода, убранные вручную области)."""
        image = stretch(-(background - frame), *self.gray_range)
        manual = np.zeros(image.shape, dtype=bool)
        for row_from, row_to, col_from, col_to in self.erase_regions:
            manual[row_from - 1 : row_to, col_from - 1 : col_to] = True
        image[manual] = 1.0  # как в MATLAB: im{3}(строки, столбцы) = 1 — «вручную убираем ненужные области»
        dark = (1.0 - image) > self.binarize_threshold
        closed = ops.close_(dark, self.close_disk)
        closed[self.surface_row - 1 :, :] = True  # водная поверхность
        filled = ops.fill_holes(closed)
        labels, object_areas = ops.areas(filled)
        is_big = np.concatenate([[False], object_areas > rim_area])
        mask = ops.dilate(is_big[labels], self.dilate_disk)
        image = image.copy()
        image[mask] = 1.0
        return image, mask | manual


class DropletFinder:
    """Находит капли на кадре с удалённым римом: центры (N, 2: x, y; px с 1) и радиусы (N,), px.

    Шаги: инверсия -> заливка дыр -> растяжение яркости -> границы canny -> заливка контуров ->
    отделение «линий» (imopen) -> удаление мелочи -> закрытие и заливка -> объекты.
    Радиус — эквивалентный: круг той же площади.

    gray_range — растяжение яркости перед canny (DROPLET_GRAY_RANGE); canny_threshold — порог границ;
    open_radius, close_radius — радиусы дисков, px; min_pixels — контуры меньше этого — шум.
    MATLAB: блок «Выделение капель» st1_droplets_separation.m (im_e{...}).
    """

    def __init__(self, gray_range: tuple[float, float], canny_threshold: float,
                 open_radius: int, min_pixels: int, close_radius: int) -> None:
        self.gray_range = gray_range
        self.canny_threshold = canny_threshold
        self.open_disk = ops.disk(open_radius)
        self.min_pixels = min_pixels
        self.close_disk = ops.disk(close_radius)

    @classmethod
    def from_thresholds(cls, t: ModuleType) -> "DropletFinder":
        """Из раздела «поиск капель» thresholds.py эксперимента."""
        return cls(t.DROPLET_GRAY_RANGE, t.CANNY_THRESHOLD, t.DROPLET_OPEN_RADIUS, t.DROPLET_MIN_PIXELS,
                   t.DROPLET_CLOSE_RADIUS)

    def find(self, clean: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Капли на кадре без рима clean (результат RimRemover.remove): центры (N, 2: x, y; px с 1)
        и радиусы (N,), px. Порядок капель — как у MATLAB (по столбцам кадра)."""
        filled = ops.fill_holes_gray(1.0 - clean)
        # ВОПРОС К СПЕЦИАЛИСТУ (Q7, см. QUESTIONS.md). Как в MATLAB («костыль1»): в углу кадра рисуется
        # полоска 0/1. canny делит градиент на его максимум по кадру, а градиент этой полоски (0.733)
        # больше любой настоящей границы — так порог canny фактически становится абсолютным:
        # 0.1 × 0.733 = 0.0733 в единицах яркости. Чище — абсолютный порог без подрисовки.
        filled[0:2, 0:5] = 0
        filled[2:4, 0:5] = 1
        edges = ops.canny(stretch(filled, *self.gray_range), self.canny_threshold)
        edges[0:6, 0:6] = False  # «костыль2» MATLAB: стираем границы, нарисованные полоской
        contours = ops.fill_holes(edges)
        solid = ops.open_(contours, self.open_disk)  # сплошные объекты (без тонких линий)
        rings = contours & ~solid  # только контуры
        rings = ops.remove_small(rings, self.min_pixels)
        rings = ops.fill_holes(ops.close_(rings, self.close_disk))
        centers, diameters = ops.centroids_and_diameters(rings | solid)
        return centers, diameters / 2.0
