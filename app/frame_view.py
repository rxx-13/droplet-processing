"""Кадр в приложении: показ видов 1–5 (pyqtgraph), шкала кадров записи, расчёт кадров в фоне.

КАК показывать — здесь; ЧТО показывать (расчёт st1 для кадра: удаление рима, капли, PIV) — в
lib/st1/preview.py (compute_frame, FrameJob, FrameResult, VIEWS). Виды — как в tools/st1_viewer.py:
    frame       исходный кадр
    clean       кадр без рима и воды; закрашенное подсвечено сиреневым
    droplets    капли: Python — красные круги, MATLAB — голубые пунктирные
    piv         смещения PIV поверх кадра (зелёные стрелки)
    piv_matlab  PIV как на картинке MATLAB: клетки — скорость PIV (м/с), красные стрелки — PIV,
                синие — скорость капель, чёрные круги — капли
Координаты — пиксели с 1, как в MATLAB: центр пикселя (строка i, столбец j) — точка (x, y) = (j, i).
Колесо мыши — увеличение, перетаскивание — сдвиг; увеличение сохраняется при листании.

FrameComputer считает кадры (compute_frame) в фоновых процессах: нужный сейчас — первым, затем остальные
по списку. При смене записи, rim или порогов прежние расчёты отменяются и забываются.
"""

import os
from concurrent.futures import Future, ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from typing import Any

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPaintEvent, QPen, QPolygonF
from PySide6.QtWidgets import QWidget

import pyqtgraph as pg  # после PySide6: pyqtgraph берёт уже загруженную библиотеку Qt

from lib.st1.piv import PivGrid
from lib.st1.preview import ERASED_COLOR, FrameJob, FrameResult, compute_frame

pg.setConfigOptions(imageAxisOrder="row-major")  # массив кадра — (строки, столбцы), как в numpy

PIV_VIEWS = {"piv", "piv_matlab"}  # виды, которым нужен PIV (расчёт пары кадров)
BACKGROUND = "#202020"  # фон вокруг кадра
ERASED_RGBA = (*QColor(ERASED_COLOR).getRgb()[:3], 90)  # подсветка закрашенного, прозрачность ~0.35
MAX_RESULTS = 250  # больше посчитанных кадров не хранить (память): дальние от текущего забываются


# ------------------------------------------------------------------ расчёт кадров


class FrameComputer:
    """Расчёт кадров st1 (compute_frame) для показа.

    workers — сколько процессов считают кадры (по умолчанию все ядра, кроме одного): один — только текущий
    кадр (чтобы, например, после правки rim не ждать, пока освободятся остальные), прочие — кадры впрок.
    0 — без процессов: кадр считается сразу при запросе (окно на это время замирает; так делают тесты).
    Номера кадров — номера файлов записи, с 1.
    """

    def __init__(self, workers: int | None = None) -> None:
        self.workers = max(2, (os.cpu_count() or 2) - 1) if workers is None else workers
        self.urgent: ProcessPoolExecutor | None = None  # текущий кадр
        self.executor: ProcessPoolExecutor | None = None  # кадры впрок
        if self.workers > 0:
            self._start_pools()
        self.params: dict[str, Any] | None = None  # всё для FrameJob, кроме номера кадра и пары
        self.count = 0  # файлов в записи
        self.results: dict[int, FrameResult] = {}
        self.errors: dict[int, str] = {}
        self.futures: dict[tuple[int, bool], Future] = {}  # (кадр, с PIV) -> расчёт в процессе
        self.urgent_keys: set[tuple[int, bool]] = set()  # что отправлено в процесс текущего кадра

    def _start_pools(self) -> None:
        """Процессы: один для текущего кадра, остальные (не меньше одного) — для кадров впрок."""
        self.urgent = ProcessPoolExecutor(1)
        self.executor = ProcessPoolExecutor(max(1, self.workers - 1))

    def configure(self, images_dir: str, count: int, background_number: int, rim_area: float,
                  surface_row: int, erase_regions: Any, thresholds: dict[str, Any]) -> None:
        """Параметры расчёта записи (как у FrameJob); если что-то поменялось — посчитанное забывается."""
        params = {"images_dir": images_dir, "background_number": background_number, "rim_area": float(rim_area),
                  "surface_row": surface_row, "erase_regions": tuple(erase_regions), "thresholds": thresholds}
        if params != self.params or count != self.count:
            self.cancel()
            self.results.clear()
            self.errors.clear()
            self.params, self.count = params, count

    def _job(self, number: int, with_piv: bool) -> FrameJob:
        """Задание на кадр number; PIV — до следующего файла (у последнего — от предыдущего, как в st1)."""
        pair = None
        if with_piv and self.count >= 2:
            first = min(number, self.count - 1)
            pair = (first, first + 1)
        return FrameJob(k=number, frame_number=number, pair=pair, **self.params)

    def _cached(self, number: int, with_piv: bool) -> FrameResult | None:
        """Посчитанный кадр, если он годится (для видов PIV — посчитанный с PIV)."""
        result = self.results.get(number)
        if result is None or (with_piv and result.pair is None and self.count >= 2):
            return None
        return result

    @property
    def parallel(self) -> bool:
        """Считаются ли кадры в процессах (False — сразу при запросе)."""
        return self.executor is not None

    def ready(self, number: int, with_piv: bool) -> bool:
        """Посчитан ли кадр (без ожидания)."""
        return self._cached(number, with_piv) is not None

    def result(self, number: int, with_piv: bool) -> FrameResult | None:
        """Посчитанный кадр или None (ещё считается); без процессов — считается сразу."""
        cached = self._cached(number, with_piv)
        if cached is not None or self.executor is not None or self.params is None:
            return cached
        self.results[number] = compute_frame(self._job(number, with_piv))
        return self.results[number]

    def _submit(self, key: tuple[int, bool], urgent: bool) -> None:
        """Отправить кадр key в процесс текущего кадра (urgent) или в процессы кадров впрок."""
        try:
            pool = self.urgent if urgent else self.executor
            self.futures[key] = pool.submit(compute_frame, self._job(*key))
        except BrokenProcessPool:  # процесс расчёта упал — запустить процессы заново
            self.cancel()
            self._start_pools()
            self.futures[key] = (self.urgent if urgent else self.executor).submit(compute_frame, self._job(*key))
        if urgent:
            self.urgent_keys.add(key)

    def focus(self, numbers: list[int], with_piv: bool) -> None:
        """Считать кадры numbers: первый (нужный сейчас) — в отдельном процессе, остальные — впрок, по порядку.
        Прежний «текущий» кадр, если его ещё не начали считать, отменяется."""
        if self.executor is None or self.params is None:
            return
        wanted = [(n, with_piv) for n in numbers if not self.ready(n, with_piv) and n not in self.errors]
        if not wanted:
            return
        first = wanted[0]
        future = self.futures.get(first)
        if future is None or (first not in self.urgent_keys and future.cancel()):  # ещё не считается
            for key in list(self.urgent_keys):
                if key in self.futures and self.futures[key].cancel():
                    del self.futures[key]
            self.urgent_keys &= set(self.futures)
            self._submit(first, urgent=True)
        for key in wanted[1:]:
            if key not in self.futures:
                self._submit(key, urgent=False)

    def cancel_waiting(self) -> None:
        """Отменить кадры впрок, которые ещё не начали считаться (начатые досчитаются)."""
        for key, future in list(self.futures.items()):
            if key not in self.urgent_keys and future.cancel():
                del self.futures[key]

    def collect(self, current: int = 1) -> bool:
        """Забрать готовые кадры из процессов; True — появилось новое. current — текущий кадр (если
        посчитанных слишком много, забываются дальние от него)."""
        new = False
        for key, future in list(self.futures.items()):
            if not future.done():
                continue
            del self.futures[key]
            self.urgent_keys.discard(key)
            if future.cancelled():
                continue
            try:
                result = future.result()
            except Exception as error:  # ошибка расчёта — показать её вместо кадра
                self.errors[key[0]] = f"{type(error).__name__}: {error}"
            else:
                old = self.results.get(key[0])
                if old is None or old.pair is None:
                    self.results[key[0]] = result
            new = True
        if len(self.results) > MAX_RESULTS:
            for number in sorted(self.results, key=lambda n: abs(n - current))[MAX_RESULTS:]:
                del self.results[number]
        return new

    def busy(self) -> bool:
        """Идёт ли расчёт в процессах."""
        return bool(self.futures)

    def cancel(self) -> None:
        """Отменить расчёты (уже начатые досчитаются, но их результат не возьмётся)."""
        for future in self.futures.values():
            future.cancel()
        self.futures = {}
        self.urgent_keys = set()

    def shutdown(self) -> None:
        """Остановить процессы расчёта (при закрытии окна)."""
        self.cancel()
        for pool in (self.urgent, self.executor):
            if pool is not None:
                pool.shutdown(wait=False, cancel_futures=True)


# ------------------------------------------------------------------ показ кадра


def _arrow_segments(x: np.ndarray, y: np.ndarray, dx: np.ndarray, dy: np.ndarray,
                    scale: float) -> tuple[np.ndarray, np.ndarray]:
    """Стрелки из (x, y) длиной (dx, dy) × scale как пары точек (для connect="pairs"): древко и два уса."""
    x1, y1 = x + dx * scale, y + dy * scale
    angle = np.arctan2(y1 - y, x1 - x)
    head = 0.3 * np.hypot(x1 - x, y1 - y)
    ends = [(x, y), (x1, y1)]
    for turn in (-0.45, 0.45):
        ends += [(x1, y1), (x1 - head * np.cos(angle + turn), y1 - head * np.sin(angle + turn))]
    xs = np.column_stack([e[0] for e in ends]).ravel()
    ys = np.column_stack([e[1] for e in ends]).ravel()
    return xs, ys


def _levels(picture: np.ndarray) -> tuple[float, float]:
    """Уровни яркости кадра для показа: от самого тёмного до самого светлого пикселя."""
    low, high = float(picture.min()), float(picture.max())
    return low, (high if high > low else low + 1.0)


class FrameView(pg.GraphicsLayoutWidget):
    """Кадр с наложениями (виды 1–5) и крестиком «центра затравки».

    clicked(x, y) — щелчок левой кнопкой по кадру, координаты в пикселях с 1 (дробные).
    """

    clicked = Signal(float, float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setBackground(BACKGROUND)
        self.box = self.addViewBox(row=0, col=0)
        self.box.setAspectLocked(True)
        self.box.invertY(True)  # ось y вниз, как у кадра
        self.box.setMenuEnabled(False)
        self.image = pg.ImageItem()
        self.speed = pg.ImageItem()  # клетки скорости PIV (вид 5)
        self.erased = pg.ImageItem()  # подсветка закрашенного (вид 2)
        self.circles = pg.ScatterPlotItem(pxMode=False, symbol="o", brush=pg.mkBrush(None))
        self.matlab_circles = pg.ScatterPlotItem(pxMode=False, symbol="o", brush=pg.mkBrush(None),
                                                 pen=pg.mkPen("c", width=1.2, style=Qt.PenStyle.DashLine))
        self.arrows = pg.PlotCurveItem()
        self.droplet_arrows = pg.PlotCurveItem(pen=pg.mkPen("b", width=1.2))
        self.center = pg.ScatterPlotItem(pxMode=True, symbol="+", size=28, pen=pg.mkPen("#000000", width=1),
                                         brush=pg.mkBrush("#ffb000"))
        for z, item in enumerate((self.image, self.speed, self.erased, self.arrows, self.droplet_arrows,
                                  self.circles, self.matlab_circles, self.center)):
            item.setZValue(z)
            self.box.addItem(item)
        self.colorbar: pg.ColorBarItem | None = None
        self.colorbar_shown = False
        self.frame_size: tuple[int, int] | None = None  # (высота, ширина) показанного кадра
        self.scene().sigMouseClicked.connect(self._mouse_clicked)

    # --- основа и наложения
    def _base(self, picture: np.ndarray, low: float, high: float) -> None:
        """Картинка-основа; при смене размера кадра показывается весь кадр."""
        h, w = picture.shape
        self.image.setImage(picture, levels=(low, high), autoLevels=False)
        self.image.setRect(QRectF(0.5, 0.5, w, h))
        self.image.setVisible(True)
        if self.frame_size != (h, w):
            self.frame_size = (h, w)
            self.fit()

    def _overlay(self, item: pg.ImageItem, rgba: np.ndarray, rect: QRectF) -> None:
        """Цветная полупрозрачная картинка поверх кадра (массив строки × столбцы × RGBA)."""
        item.setImage(rgba, levels=(0, 255), autoLevels=False)
        item.setRect(rect)
        item.setVisible(True)

    def _clear(self) -> None:
        """Убрать наложения прошлого кадра (крестик центра остаётся)."""
        empty = np.zeros(0)
        self.circles.clear()
        self.matlab_circles.clear()
        self.arrows.setData(empty, empty)
        self.droplet_arrows.setData(empty, empty)
        self.erased.setVisible(False)
        self.speed.setVisible(False)
        self.setBackground(BACKGROUND)
        if self.colorbar_shown:
            self.removeItem(self.colorbar)
            self.colorbar_shown = False

    @staticmethod
    def _draw_circles(item: pg.ScatterPlotItem, centers: np.ndarray, radii: np.ndarray, color: str | None) -> int:
        """Окружности капель (центры (N, 2), радиусы, px); color None — цвет, заданный при создании."""
        c = np.reshape(centers, (-1, 2))
        r = np.ravel(radii)
        if color is None:
            item.setData(x=c[:, 0], y=c[:, 1], size=2 * r)
        else:
            item.setData(x=c[:, 0], y=c[:, 1], size=2 * r, pen=pg.mkPen(color, width=1.2))
        return len(r)

    @staticmethod
    def _draw_arrows(item: pg.PlotCurveItem, x: np.ndarray, y: np.ndarray, dx: np.ndarray, dy: np.ndarray,
                     scale: float, color: str) -> None:
        """Стрелки (x, y) -> (x, y) + (dx, dy) × scale."""
        xs, ys = _arrow_segments(x, y, dx, dy, scale)
        item.setData(xs, ys, connect="pairs", pen=pg.mkPen(color, width=1.2))

    def _speed_cells(self, grid: PivGrid, speed: np.ndarray, speed_max: float) -> None:
        """Клетки скорости PIV вокруг узлов сетки (цвет jet от 0 до speed_max) и цветовая шкала."""
        nx, ny = grid.shape
        sx = float(grid.x_arr[1] - grid.x_arr[0]) if nx > 1 else 1.0
        sy = float(grid.y_arr[1] - grid.y_arr[0]) if ny > 1 else 1.0
        values = speed.T  # (ny, nx): строки — y
        jet = pg.colormap.get("jet", source="matplotlib")
        scaled = np.clip(np.nan_to_num(values) / speed_max, 0.0, 1.0)
        rgba = jet.map(scaled.ravel(), mode="byte").reshape(ny, nx, 4)
        rgba[np.isnan(values)] = 0
        self._overlay(self.speed, rgba, QRectF(grid.x_arr[0] - sx / 2, grid.y_arr[0] - sy / 2, nx * sx, ny * sy))
        if self.colorbar is None:
            self.colorbar = pg.ColorBarItem(values=(0, speed_max), colorMap=jet, interactive=False,
                                            label="скорость PIV, м/с")
        self.colorbar.setLevels((0, speed_max))
        self.addItem(self.colorbar, row=0, col=1)
        self.colorbar_shown = True

    # --- виды
    def show(self, view: str, frame: np.ndarray, result: FrameResult | None = None, *,
             matlab: tuple[np.ndarray, np.ndarray] | None = None, arrow_scale: float = 10.0,
             speed_max: float = 7.0, mm_per_px: float = 1.0, fps: float = 1.0) -> str:
        """Нарисовать вид view: frame — исходный кадр (уровни файла); result — расчёт кадра (для вида
        frame не нужен); matlab — капли MATLAB (центры, радиусы) или None; arrow_scale — длина стрелок
        PIV на 1 px/кадр; speed_max — верх шкалы скорости, м/с; mm_per_px, fps — для перевода в м/с.
        Возвращает подпись к виду."""
        self._clear()
        self._base(frame, *_levels(frame))
        if view == "frame" or result is None:
            return ""
        if view == "clean":
            self._base(result.clean, 0, 255)
            mask = result.erased_mask
            rgba = np.zeros((*mask.shape, 4), dtype=np.uint8)
            rgba[mask] = ERASED_RGBA
            self._overlay(self.erased, rgba, QRectF(0.5, 0.5, mask.shape[1], mask.shape[0]))
            return "сиреневым — закрашено как рим, крупные брызги и вода"
        if view == "droplets":
            text = f"капель: Python {self._draw_circles(self.circles, result.centers, result.radii, 'r')} (красные)"
            if matlab is not None:
                text += f", MATLAB {self._draw_circles(self.matlab_circles, *matlab, None)} (голубые)"
            return text
        piv = result.piv
        if piv is None:
            return "PIV нет: в записи один кадр"
        a, b = result.pair
        pair_text = f"PIV файлов {a}→{b}"
        ok = ~np.isnan(piv.xdispl) & ~np.isnan(piv.ydispl)
        x, y, dx, dy = piv.grid.x_mat[ok], piv.grid.y_mat[ok], piv.xdispl[ok], piv.ydispl[ok]
        if view == "piv":
            self._draw_arrows(self.arrows, x, y, dx, dy, arrow_scale, "#00ff00")
            return f"{pair_text}: векторов после фильтра {ok.sum()} из {ok.size}; стрелка = смещение × {arrow_scale}"
        if view == "piv_matlab":
            self.image.setVisible(False)  # как в MATLAB: белый фон
            self.setBackground("w")
            speed = np.hypot(piv.xdispl, piv.ydispl) * mm_per_px / 1000 * fps  # м/с
            self._speed_cells(piv.grid, speed, speed_max)
            self._draw_arrows(self.arrows, x, y, dx, dy, arrow_scale, "r")
            has = ~np.isnan(result.xd)
            centers = np.reshape(result.centers, (-1, 2))
            if has.any():
                self._draw_arrows(self.droplet_arrows, centers[has, 0], centers[has, 1], result.xd[has],
                                  result.yd[has], arrow_scale, "b")
            self._draw_circles(self.circles, result.centers, result.radii, "k")
            return f"{pair_text}: красные — PIV, синие — скорость капель (× {arrow_scale})"
        raise ValueError(f"Неизвестный вид {view!r}")

    def clear_frame(self) -> None:
        """Убрать кадр и наложения (например, если у записи нет кадров)."""
        self._clear()
        self.image.setVisible(False)
        self.center.clear()

    def set_center(self, x: float | None, y: float | None) -> None:
        """Крестик «центра затравки» в точке (x, y), px с 1; None — убрать."""
        if x is None or y is None:
            self.center.clear()
        else:
            self.center.setData([x], [y])

    def fit(self) -> None:
        """Показать кадр целиком."""
        if self.frame_size is not None:
            h, w = self.frame_size
            self.box.setRange(QRectF(0.5, 0.5, w, h), padding=0.01)

    def _mouse_clicked(self, event: Any) -> None:
        """Щелчок левой кнопкой по кадру (не перетаскивание) -> сигнал clicked с координатами кадра."""
        if event.button() != Qt.MouseButton.LeftButton or event.double() or self.frame_size is None:
            return
        if not self.box.sceneBoundingRect().contains(event.scenePos()):
            return
        point = self.box.mapSceneToView(event.scenePos())
        h, w = self.frame_size
        if 0.5 <= point.x() <= w + 0.5 and 0.5 <= point.y() <= h + 0.5:
            self.clicked.emit(point.x(), point.y())


# ------------------------------------------------------------------ шкала кадров


class FrameScale(QWidget):
    """Шкала кадров записи: все файлы; диапазон start_frame…fin_frame — зелёным; посчитанные кадры —
    серыми рисками; текущий кадр — синим. Щелчок или перетаскивание — перейти к кадру (chosen)."""

    chosen = Signal(int)
    MARGIN = 10

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(40)
        self.count, self.current = 0, 1
        self.start: int | None = None
        self.fin: int | None = None
        self.computed: set[int] = set()

    def set_state(self, count: int, current: int, start: int | None, fin: int | None,
                  computed: set[int] | None = None) -> None:
        """Число файлов, текущий кадр, диапазон (None — не задан), посчитанные кадры."""
        self.count, self.current, self.start, self.fin = count, current, start, fin
        self.computed = set(computed or ())
        self.update()

    def _bar(self) -> QRectF:
        """Прямоугольник полосы."""
        return QRectF(self.MARGIN, 17, self.width() - 2 * self.MARGIN, self.height() - 21)

    def _x(self, edge: float) -> float:
        """x границы кадров: edge = 0 — левый край первого кадра, edge = count — правый край последнего."""
        bar = self._bar()
        return bar.left() + edge / max(self.count, 1) * bar.width()

    def paintEvent(self, event: QPaintEvent) -> None:
        """Нарисовать шкалу."""
        p = QPainter(self)
        bar = self._bar()
        p.fillRect(bar, QColor("#d0d0d0"))
        if not self.count:
            return
        if self.start is not None and self.fin is not None and self.fin >= self.start:
            first, last = max(self.start, 1), min(self.fin, self.count)
            if last >= first:
                p.fillRect(QRectF(self._x(first - 1), bar.top(), self._x(last) - self._x(first - 1), bar.height()),
                           QColor("#8fd18f"))
        p.setPen(QPen(QColor("#707070"), 1))
        for n in self.computed:
            x = self._x(n - 0.5)
            p.drawLine(QPointF(x, bar.bottom() - 5), QPointF(x, bar.bottom()))
        p.setPen(QColor("#404040"))
        p.drawText(QRectF(0, 0, 60, 16), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, " 1")
        p.drawText(QRectF(self.width() - 60, 0, 60, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                   f"{self.count} ")
        x = self._x(self.current - 0.5)
        p.setPen(QPen(QColor("#1f5fd0"), 2))
        p.drawLine(QPointF(x, bar.top()), QPointF(x, bar.bottom()))
        p.setBrush(QColor("#1f5fd0"))
        p.drawPolygon(QPolygonF([QPointF(x - 5, bar.top() - 6), QPointF(x + 5, bar.top() - 6), QPointF(x, bar.top())]))
        p.drawText(QRectF(x - 40, 0, 80, 12), Qt.AlignmentFlag.AlignCenter, str(self.current))

    def _choose(self, event: QMouseEvent) -> None:
        """Кадр под мышью -> сигнал chosen."""
        if self.count:
            bar = self._bar()
            n = int((event.position().x() - bar.left()) / bar.width() * self.count) + 1
            self.chosen.emit(min(max(n, 1), self.count))

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Щелчок по шкале."""
        self._choose(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """Перетаскивание по шкале."""
        self._choose(event)
