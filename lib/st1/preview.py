"""st1: вспомогательные картинки — ЧТО показывать (кадр, кадр без рима, капли, PIV).

Здесь только расчёт и рисование на заданные оси matplotlib — без окон, клавиш и файлов.
КАК показывать (окно, серия PNG, слайдшоу, картинки во время st1) решают короткие оболочки над
RecordPreview и ViewDrawer; сейчас одна — окно tools/st1_viewer.py. Новый способ просмотра —
новая оболочка, этот файл менять не нужно.

Виды (VIEWS):
    frame       исходный кадр
    clean       кадр после удаления рима, крупных брызг и воды — в нём ищутся капли;
                закрашенное подсвечено сиреневым (видно, какие капли пропали вместе с римом)
    droplets    исходный кадр с каплями: Python — красные круги, MATLAB — голубые
                (если в settings задан MATLAB_RESULTS_DIR и там есть particles.mat этой записи)
    piv         исходный кадр со смещениями PIV (после фильтра) до следующего кадра
    piv_matlab  PIV как на картинке MATLAB-скрипта st1: клетки — скорость PIV в м/с, красные
                стрелки — PIV, синие — скорость в центрах капель, чёрные круги — капли

Расчёт кадра (compute_frame) — та же обработка, что в main/st1_droplets_separation.py, с порогами
из thresholds.py. Он не зависит от окна, поэтому кадры можно считать заранее в других процессах
(RecordPreview.submit_all). Координаты на картинках — пиксели с 1, как в MATLAB и в particles.mat.
Готовые результаты st1 (капли и PIV из файлов, без пересчёта) — SavedResults.
"""

import functools
import logging
from concurrent.futures import Executor, Future
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import numpy as np
from matplotlib.axes import Axes
from matplotlib.cm import ScalarMappable
from matplotlib.collections import PatchCollection
from matplotlib.colors import ListedColormap, Normalize
from matplotlib.patches import Circle
from scipy.io import loadmat

from ..common import files
from ..common.records import Record
from .droplets import DropletFinder, RimRemover
from .piv import Piv, PivGrid, PivResult, filter_piv_by_thresholds
from .velocity import velocity_at_points

log = logging.getLogger(__name__)

VIEWS = {"frame": "исходный кадр", "clean": "без рима и воды", "droplets": "капли",
         "piv": "PIV поверх кадра", "piv_matlab": "PIV как в MATLAB"}
COMPUTED_VIEWS = {"clean", "droplets", "piv", "piv_matlab"}  # нужен расчёт кадра
ERASED_COLOR = "#b04ad8"  # подсветка закрашенного как рим/вода


# ------------------------------------------------------------------ расчёт кадра


@dataclass(frozen=True)
class FrameJob:
    """Всё, что нужно для расчёта кадра k (только простые данные — их можно передать в другой процесс)."""

    k: int
    images_dir: str
    frame_number: int  # номер файла кадра k
    pair: tuple[int, int] | None  # номера файлов пары для PIV (k, k+1); у последнего кадра — (N−1, N)
    background_number: int
    rim_area: float
    surface_row: int
    erase_regions: tuple
    thresholds: dict[str, Any]


@dataclass(frozen=True)
class FrameResult:
    """Результат st1 для кадра k (как в st1) и данные для картинок."""

    clean: np.ndarray  # кадр без рима, 0..255 (uint8)
    erased: np.ndarray  # маска закрашенного, упакованная np.packbits
    shape: tuple[int, int]
    centers: np.ndarray  # (N, 2): x, y; px с 1
    radii: np.ndarray  # (N,), px
    piv: PivResult | None  # после фильтра, пара job.pair
    pair: tuple[int, int] | None
    xd: np.ndarray  # скорость PIV в центрах капель, px/кадр
    yd: np.ndarray

    @property
    def erased_mask(self) -> np.ndarray:
        """Маска закрашенного (рим, брызги, вода, ручные области) как bool-массив формы shape."""
        return np.unpackbits(self.erased)[: self.shape[0] * self.shape[1]].reshape(self.shape).astype(bool)


@functools.lru_cache(maxsize=4)
def _frames(images_dir: str) -> files.Frames:
    """Кадры записи; запоминаются в процессе, чтобы не перечитывать папку для каждого кадра."""
    return files.Frames(Path(images_dir))


@functools.lru_cache(maxsize=4)
def _background(images_dir: str, number: int) -> np.ndarray:
    """Кадр фона; запоминается в процессе."""
    return _frames(images_dir).gray(number)


def compute_frame(job: FrameJob) -> FrameResult:
    """st1 для одного кадра: удаление рима, капли, PIV до следующего кадра, скорость в центрах капель."""
    t = SimpleNamespace(**job.thresholds)
    frames = _frames(job.images_dir)
    background = _background(job.images_dir, job.background_number)
    rim = RimRemover.from_thresholds(t, job.surface_row, job.erase_regions)
    clean, erased = rim.remove_with_mask(frames.gray(job.frame_number), background, job.rim_area)
    centers, radii = DropletFinder.from_thresholds(t).find(clean)

    piv = None
    xd, yd = np.full(len(radii), np.nan), np.full(len(radii), np.nan)
    if job.pair is not None:
        def cleaned(number: int) -> np.ndarray:
            return clean if number == job.frame_number else rim.remove(frames.gray(number), background, job.rim_area)

        a, b = job.pair
        raw = Piv.from_thresholds(t, frames.width, frames.height).compute(1.0 - cleaned(a), 1.0 - cleaned(b))
        piv = filter_piv_by_thresholds(raw, t)
        if len(radii):
            xd, yd = velocity_at_points(centers, piv)
    return FrameResult(np.round(clean * 255).astype(np.uint8), np.packbits(erased), clean.shape,
                       centers, radii, piv, job.pair, xd, yd)


# ------------------------------------------------------------------ готовые результаты st1


def _load_piv(path: Path) -> list[PivResult | None]:
    """PIV после фильтрации из PIV_output_filtered_arr.mat (результат st1) по парам кадров; пустая ячейка — None."""
    cell = loadmat(path, squeeze_me=True, struct_as_record=False)["PIV_output_filtered_arr"]
    found: list[PivResult | None] = []
    for item in np.atleast_1d(cell):
        if not hasattr(item, "xdispl"):
            found.append(None)
            continue
        g = item.PIV_params.piv_grid
        grid = PivGrid(np.atleast_1d(g.xArr).astype(np.float64), np.atleast_1d(g.yArr).astype(np.float64))

        def array(values: Any) -> np.ndarray:
            return np.asarray(values, dtype=np.float64).reshape(grid.shape)

        found.append(PivResult(array(item.status), array(item.xdispl), array(item.ydispl), array(item.CC_maxValue),
                               array(item.CC_maxRaitio), array(item.mean_im1), grid, {},
                               tuple(int(v) for v in np.atleast_1d(item.imSize)), float(item.time_of_iteration)))
    return found


class SavedResults:
    """Готовые результаты st1 записи — капли и PIV по кадрам диапазона из particles.mat
    и PIV_output_filtered_arr.mat, чтобы показывать их без пересчёта (виды droplets, piv, piv_matlab).

    st1_dir — папка результата st1 записи (Record.st1_dir). Кадра без рима в результатах нет — вид clean
    всегда считается. Какими порогами посчитаны результаты, в файлах не записано; modified — когда записан
    particles.mat (время в секундах, как os.stat).
    """

    def __init__(self, st1_dir: Path) -> None:
        self.centers, self.radii, self.xd, self.yd = files.load_particles(st1_dir)
        self.modified = (st1_dir / "particles.mat").stat().st_mtime
        piv_path = st1_dir / "PIV_output_filtered_arr.mat"
        self.piv = _load_piv(piv_path) if piv_path.exists() else []

    def __len__(self) -> int:
        return len(self.centers)

    def result(self, k: int, first_number: int) -> FrameResult:
        """Кадр k диапазона (с 1) как FrameResult (без кадра без рима); first_number — номер файла первого
        кадра диапазона. PIV — пары (k, k+1), у последнего кадра — предыдущей пары, как в st1."""
        p = min(k, len(self.centers) - 1) - 1  # номер пары PIV, с 0
        piv = self.piv[p] if 0 <= p < len(self.piv) else None
        pair = None if piv is None else (first_number + p, first_number + p + 1)
        return FrameResult(np.zeros((0, 0), dtype=np.uint8), np.packbits(np.zeros(0, dtype=bool)), (0, 0),
                           self.centers[k - 1], self.radii[k - 1], piv, pair, self.xd[k - 1], self.yd[k - 1])


# ------------------------------------------------------------------ данные записи


class RecordPreview:
    """Кадры одной записи: исходные — с диска, посчитанные — по требованию или заранее в фоне.

    k — номер кадра в диапазоне записи, с 1 (k = 1 — файл start_frame).
    """

    def __init__(self, record: Record, settings: ModuleType, thresholds: ModuleType) -> None:
        if record.rim is None:
            raise ValueError(f"{record.name}: в таблице не задан порог рима (колонка rim / rim_new)")
        self.record = record
        self.frames = files.Frames(record.images_dir(Path(settings.IMAGES_DIR)))
        self.numbers = list(range(record.start_frame, record.fin_frame + 1))  # номера файлов кадров
        self.mm_per_px, self.fps = settings.SCALE_MM_PER_PX, settings.FPS
        self.arrow_scale = thresholds.PIV_PLOT_ARROW_SCALE
        self.speed_max = thresholds.PIV_PLOT_SPEED_MAX
        self.matlab = self._load_matlab(getattr(settings, "MATLAB_RESULTS_DIR", None))
        values = {name: getattr(thresholds, name) for name in dir(thresholds) if name.isupper()}
        n = len(self.numbers)
        self._jobs = {k: FrameJob(
            k=k, images_dir=str(record.images_dir(Path(settings.IMAGES_DIR))), frame_number=self.numbers[k - 1],
            pair=None if n < 2 else (self.numbers[min(k, n - 1) - 1], self.numbers[min(k, n - 1)]),
            background_number=settings.BACKGROUND_FRAME, rim_area=record.rim,
            surface_row=settings.SURFACE_ROW, erase_regions=tuple(settings.ERASE_REGIONS),
            thresholds=values) for k in range(1, n + 1)}
        self._results: dict[int, FrameResult] = {}
        self._futures: dict[int, Future] = {}

    def __len__(self) -> int:
        return len(self.numbers)

    def _load_matlab(self, matlab_dir: str | None) -> tuple[list[np.ndarray], list[np.ndarray]] | None:
        """Капли MATLAB по кадрам, если есть его particles.mat для этой записи."""
        if not matlab_dir:
            return None
        st1_dir = self.record.st1_dir(Path(matlab_dir))
        if not (st1_dir / "particles.mat").exists():
            return None
        centers, radii, _, _ = files.load_particles(st1_dir)
        if len(centers) != len(self.numbers):
            log.warning("%s: у MATLAB %d кадров, в таблице %d — капли MATLAB не показываются",
                        self.record.name, len(centers), len(self.numbers))
            return None
        return centers, radii

    def frame(self, k: int) -> np.ndarray:
        """Исходный кадр k."""
        return self.frames.gray(self.numbers[k - 1])

    def matlab_droplets(self, k: int) -> tuple[np.ndarray, np.ndarray] | None:
        """Капли MATLAB на кадре k: центры (N, 2) и радиусы, px; None, если результата MATLAB нет."""
        return None if self.matlab is None else (self.matlab[0][k - 1], self.matlab[1][k - 1])

    # --- расчёт
    def submit_all(self, executor: Executor, first: int = 1) -> None:
        """Отправить в executor все ещё не посчитанные кадры, начиная с кадра first."""
        n = len(self)
        for k in [*range(first, n + 1), *range(1, first)]:
            if k not in self._results and k not in self._futures:
                self._futures[k] = executor.submit(compute_frame, self._jobs[k])

    def collect(self) -> int:
        """Забрать посчитанные в фоне кадры; возвращает, сколько кадров готово."""
        for k, future in list(self._futures.items()):
            if future.done():
                del self._futures[k]
                if not future.cancelled():
                    self._results[k] = future.result()  # ошибка расчёта всплывёт здесь
        return len(self._results)

    def ready(self, k: int) -> bool:
        """Посчитан ли уже кадр k (без ожидания)."""
        self.collect()
        return k in self._results

    def result(self, k: int) -> FrameResult:
        """Посчитанный кадр k; если его ещё нет — дождаться фонового расчёта или посчитать сейчас."""
        if k not in self._results:
            future = self._futures.pop(k, None)
            self._results[k] = future.result() if future is not None else compute_frame(self._jobs[k])
        return self._results[k]

    def cancel(self) -> None:
        """Отменить фоновый расчёт (например, при смене записи или порогов)."""
        for future in self._futures.values():
            future.cancel()
        self._futures.clear()


# ------------------------------------------------------------------ рисование


def _circles(centers: np.ndarray, radii: np.ndarray, color: str, style: str = "solid",
             width: float = 0.8) -> PatchCollection:
    """Окружности капель (центры (N, 2), радиусы, px) одним объектом для рисования."""
    patches = [Circle((x, y), r) for (x, y), r in zip(np.reshape(centers, (-1, 2)), np.ravel(radii))]
    return PatchCollection(patches, facecolor="none", edgecolor=color, linewidth=width, linestyle=style)


class ViewDrawer:
    """Рисует вид кадра на ось ax. При листании меняет только картинку и наложения, оси не
    перестраиваются — это быстро (~0.1 с на кадр) и сохраняет увеличение."""

    def __init__(self, ax: Axes) -> None:
        self.ax = ax
        self.image = None
        self.overlays: list = []
        self.colorbar = None

    def reset(self) -> None:
        """Начать заново (другая запись): оси очищаются, увеличение сбрасывается."""
        self._clear_overlays()
        self._remove_colorbar()
        self.ax.clear()
        self.image = None

    def _clear_overlays(self) -> None:
        """Убрать наложения прошлого кадра (круги, стрелки, подсветку)."""
        for artist in self.overlays:
            artist.remove()
        self.overlays = []

    def _remove_colorbar(self) -> None:
        """Убрать цветовую шкалу (она есть только у вида «PIV как в MATLAB»)."""
        if self.colorbar is not None:
            self.colorbar.remove()
            self.colorbar = None

    def _base(self, picture: np.ndarray, low: float, high: float) -> None:
        """Картинка-основа; пиксели с координатами с 1 (как в MATLAB и particles.mat)."""
        h, w = picture.shape
        if self.image is None or self.image.get_array().shape != picture.shape:
            self.ax.clear()
            self.image = self.ax.imshow(picture, cmap="gray", extent=(0.5, w + 0.5, h + 0.5, 0.5),
                                        interpolation="nearest")
            self.ax.set_autoscale_on(False)  # наложения не должны сбрасывать увеличение
        else:
            self.image.set_data(picture)
        self.image.set_clim(low, high)
        self.image.set_visible(True)

    def _add(self, artist):
        """Запомнить наложение, чтобы убрать его при следующем кадре."""
        self.overlays.append(artist)
        return artist

    def show(self, preview: RecordPreview, k: int, view: str, show_matlab: bool = True) -> str:
        """Рисует вид view кадра k. Возвращает краткую подпись для заголовка."""
        self._clear_overlays()
        if view != "piv_matlab":
            self._remove_colorbar()
        frame = preview.frame(k)
        if view == "frame":
            self._base(frame, frame.min(), frame.max())
            return ""
        result = preview.result(k)
        if view == "clean":
            self._base(result.clean, 0, 255)
            h, w = result.shape
            mask = result.erased_mask
            self._add(self.ax.imshow(np.ma.masked_where(~mask, mask), cmap=ListedColormap([ERASED_COLOR]),
                                     alpha=0.35, vmin=0, vmax=1, extent=(0.5, w + 0.5, h + 0.5, 0.5),
                                     interpolation="nearest"))
            return "сиреневым — закрашено как рим, крупные брызги и вода"
        if view == "droplets":
            self._base(frame, frame.min(), frame.max())
            self._add(self.ax.add_collection(_circles(result.centers, result.radii, "red")))
            text = f"капель: Python {len(result.radii)} (красные)"
            matlab = preview.matlab_droplets(k) if show_matlab else None
            if matlab is not None:
                self._add(self.ax.add_collection(_circles(*matlab, "cyan", style="dashed")))
                text += f", MATLAB {len(np.ravel(matlab[1]))} (голубые)"
            return text
        piv = result.piv
        if piv is None:
            self._base(frame, frame.min(), frame.max())
            return "PIV нет: в записи один кадр"
        a, b = result.pair
        pair_text = f"PIV файлов {a}→{b}"
        ok = ~np.isnan(piv.xdispl) & ~np.isnan(piv.ydispl)
        s = preview.arrow_scale
        quiver = {"angles": "xy", "scale_units": "xy", "scale": 1 / s, "width": 0.0015}
        if view == "piv":
            self._base(frame, frame.min(), frame.max())
            self._add(self.ax.quiver(piv.grid.x_mat[ok], piv.grid.y_mat[ok], piv.xdispl[ok], piv.ydispl[ok],
                                     color="lime", **quiver))
            return f"{pair_text}: векторов после фильтра {ok.sum()} из {ok.size}; стрелка = смещение × {s}"
        if view == "piv_matlab":
            self._base(frame, frame.min(), frame.max())
            self.image.set_visible(False)  # как в MATLAB: белый фон
            speed = np.hypot(piv.xdispl, piv.ydispl) * preview.mm_per_px / 1000 * preview.fps  # м/с
            norm = Normalize(0, preview.speed_max)
            self._add(self.ax.pcolormesh(piv.grid.x_mat, piv.grid.y_mat, np.ma.masked_invalid(speed),
                                         shading="nearest", cmap="jet", norm=norm))
            if self.colorbar is None:
                self.colorbar = self.ax.figure.colorbar(ScalarMappable(norm=norm, cmap="jet"), ax=self.ax,
                                                        label="скорость PIV, м/с")
            self._add(self.ax.quiver(piv.grid.x_mat[ok], piv.grid.y_mat[ok], piv.xdispl[ok], piv.ydispl[ok],
                                     color="red", **quiver))
            has = ~np.isnan(result.xd)
            centers = np.reshape(result.centers, (-1, 2))
            if has.any():
                self._add(self.ax.quiver(centers[has, 0], centers[has, 1], result.xd[has], result.yd[has],
                                         color="blue", **quiver))
            self._add(self.ax.add_collection(_circles(result.centers, result.radii, "black", width=0.5)))
            return f"{pair_text}: красные — PIV, синие — скорость капель (× {s})"
        raise ValueError(f"Неизвестный вид {view!r}; есть: {', '.join(VIEWS)}")
