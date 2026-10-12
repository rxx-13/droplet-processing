r"""Просмотр кадров st1 и подбор порогов «на глаз» — окно с увеличением и слайдшоу.

Для записей текущего эксперимента (experiments/current.py; записи — по RECORDS и CONDITIONS
из settings) показывает кадр за кадром:
    1  исходный кадр
    2  кадр без рима и воды — то, в чём ищутся капли; закрашенное подсвечено сиреневым
    3  капли: Python — красные круги, MATLAB — голубые (если задан MATLAB_RESULTS_DIR)
    4  смещения PIV поверх кадра
    5  PIV как в MATLAB: клетки — скорость PIV (м/с), красные стрелки — PIV, синие — скорость капель

Клавиши:   ← →        кадр; если держать — слайдшоу      Пробел     слайдшоу пуск / стоп
           Home End   первый / последний кадр             PgUp PgDn  предыдущая / следующая запись
           1 … 5      вид                                 M          капли MATLAB показать / скрыть
           R          перечитать settings.py и thresholds.py и пересчитать
Увеличение и сдвиг — лупа и крестик на панели окна (увеличение сохраняется при листании),
сохранить картинку — дискета там же.

Все кадры записи заранее считаются в фоне на всех ядрах процессора, кроме одного (в заголовке —
«посчитано N из M»); посчитанные кадры листаются без задержки. Если слайдшоу дошло до кадра,
который ещё считается, оно его ждёт.

Подбор порогов: поменять число в thresholds.py эксперимента, сохранить файл, нажать R в окне,
посмотреть; когда устраивает — запустить main\st1_droplets_separation.py.

Запуск:  .venv\Scripts\python.exe tools\st1_viewer.py

Что рисуется и как считается — lib/st1/preview.py; этот файл — только окно, клавиши и слайдшоу.
"""

import sys
from pathlib import Path

sys.dont_write_bytecode = True  # не создавать папки __pycache__
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # чтобы находились папки lib и experiments

import importlib  # noqa: E402
import logging  # noqa: E402
import os  # noqa: E402
import time  # noqa: E402
from concurrent.futures import ProcessPoolExecutor  # noqa: E402
from types import ModuleType  # noqa: E402

import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402

from experiments.current import settings, thresholds  # noqa: E402
from lib.common.records import Record, read_records  # noqa: E402
from lib.st1.preview import COMPUTED_VIEWS, VIEWS, RecordPreview, ViewDrawer  # noqa: E402

log = logging.getLogger("st1_viewer")

VIEW_KEYS = {"1": "frame", "2": "clean", "3": "droplets", "4": "piv", "5": "piv_matlab"}
TICK_MS = 100  # шаг таймера: слайдшоу до 10 кадров/с (если кадры посчитаны)
HOLD_DELAY_S = 0.3  # столько держать стрелку, чтобы началось слайдшоу
HELP = ("← →  кадр, держать — слайдшоу    Пробел  слайдшоу    Home End  начало / конец    "
        "PgUp PgDn  запись    1–5  вид    M  капли MATLAB    R  перечитать настройки    лупа — увеличение")


def _free_keys() -> None:
    """У matplotlib на ← → R Home свои действия (история увеличения, сброс) — освобождаем их."""
    for keymap, key in (("keymap.back", "left"), ("keymap.forward", "right"),
                        ("keymap.home", "r"), ("keymap.home", "home")):
        if key in plt.rcParams[keymap]:
            plt.rcParams[keymap].remove(key)


def _experiment_name(settings: ModuleType) -> str:
    """Имя папки эксперимента из имени модуля experiments.<эксперимент>.settings."""
    parts = getattr(settings, "__name__", "").split(".")
    return parts[1] if len(parts) >= 3 else "эксперимент"


class Viewer:
    """Окно просмотра: запись, кадр, вид и слайдшоу; расчёт и рисование — lib/st1/preview.py."""

    def __init__(self, settings: ModuleType, thresholds: ModuleType, figure: Figure | None = None,
                 workers: int | None = None) -> None:
        """workers — сколько процессов считают кадры в фоне (по умолчанию все ядра, кроме одного);
        0 — считать каждый кадр в момент показа."""
        self.settings, self.thresholds = settings, thresholds
        workers = max(1, (os.cpu_count() or 2) - 1) if workers is None else workers
        self.executor = ProcessPoolExecutor(workers) if workers > 0 else None
        self.fig = figure if figure is not None else plt.figure(figsize=(15, 8))
        if self.fig.canvas.manager is not None:
            self.fig.canvas.manager.set_window_title(f"Просмотр st1 — {_experiment_name(settings)}")
        self.ax = self.fig.add_subplot()
        self.fig.subplots_adjust(left=0.04, right=0.97, top=0.92, bottom=0.07)
        self.fig.text(0.01, 0.01, HELP, fontsize=9)
        self.drawer = ViewDrawer(self.ax)
        self.view, self.show_matlab = "droplets", True
        self.direction = 0  # слайдшоу: +1 вперёд, −1 назад, 0 — стоит
        self.held: str | None = None  # зажатая стрелка
        self.held_since = 0.0
        self.waiting = False  # показан исходный кадр, потому что нужный вид ещё считается
        self.info, self.done, self.title_time = "", 0, 0.0
        self.records = self._read_records()
        self.r = 0
        self._open(1)
        canvas = self.fig.canvas
        canvas.mpl_connect("key_press_event", lambda event: self.press(event.key))
        canvas.mpl_connect("key_release_event", lambda event: self.release(event.key))
        canvas.mpl_connect("close_event", lambda event: self.close())
        self.timer = canvas.new_timer(interval=TICK_MS)
        self.timer.add_callback(self.tick)
        self.timer.start()

    # --- записи
    def _read_records(self) -> list[Record]:
        """Записи текущего эксперимента с диапазоном кадров (по CONDITIONS и RECORDS из settings)."""
        records = [r for r in read_records(Path(self.settings.TABLE_FILE), self.settings.CONDITIONS,
                                           self.settings.RECORDS) if r.has_frames]
        if not records:
            raise SystemExit("Нет записей с диапазоном кадров, подходящих под CONDITIONS и RECORDS из settings")
        return records

    def _open(self, k: int) -> None:
        """Открыть запись self.r на кадре k и запустить фоновый расчёт её кадров."""
        log.info("Запись %s", self.records[self.r].name)
        self.preview = RecordPreview(self.records[self.r], self.settings, self.thresholds)
        self.k = min(max(k, 1), len(self.preview))
        if self.executor is not None:
            self.preview.submit_all(self.executor, self.k)
        self.done = self.preview.collect()
        self.drawer.reset()
        self.draw()

    def _switch(self, r: int, k: int) -> None:
        """Перейти к записи r, кадру k; при ошибке остаться на прежней записи и показать ошибку."""
        old, old_r = self.preview, self.r
        old.cancel()  # иначе новые кадры ждали бы в очереди за старыми
        try:
            self.r = r
            self._open(k)
        except Exception as error:  # например, нет папки с кадрами — окно не закрываем
            log.exception("Не удалось открыть запись")
            self.preview, self.r = old, old_r
            if self.executor is not None:
                old.submit_all(self.executor, self.k)
            self._error(f"Не удалось открыть запись: {error}")

    def _reload(self) -> None:
        """Перечитать settings.py и thresholds.py (после их правки) и пересчитать текущую запись."""
        try:
            if isinstance(self.settings, ModuleType):
                self.settings = importlib.reload(self.settings)
            if isinstance(self.thresholds, ModuleType):
                self.thresholds = importlib.reload(self.thresholds)
            name = self.records[self.r].name
            self.records = self._read_records()
        except Exception as error:  # ошибка в правленом файле не должна закрывать окно
            log.exception("Не удалось перечитать настройки")
            self._error(f"Ошибка в настройках: {error}")
            return
        r = next((i for i, rec in enumerate(self.records) if rec.name == name), 0)
        self._switch(r, self.k)

    # --- клавиши и таймер
    def press(self, key: str | None) -> None:
        """Нажатие клавиши (названия клавиш — как у matplotlib: "right", "pagedown", " " и т. п.)."""
        if key in ("right", "left"):
            if self.held == key:
                return  # автоповтор зажатой клавиши — слайдшоу ведёт таймер
            self.held, self.held_since, self.direction = key, time.monotonic(), 0
            self.go(self.k + (1 if key == "right" else -1))
        elif key == " ":
            self.held = None
            self.direction = 0 if self.direction else 1
        elif key == "home":
            self.go(1)
        elif key == "end":
            self.go(len(self.preview))
        elif key in ("pagedown", "pageup"):
            r = min(self.r + 1, len(self.records) - 1) if key == "pagedown" else max(self.r - 1, 0)
            if r != self.r:
                self.direction = 0
                self._switch(r, 1)
        elif key in VIEW_KEYS:
            self.view = VIEW_KEYS[key]
            self.draw()
        elif key == "m":
            self.show_matlab = not self.show_matlab
            self.draw()
        elif key == "r":
            self.direction = 0
            self._reload()

    def release(self, key: str | None) -> None:
        """Отпускание клавиши: отпустили стрелку — слайдшоу останавливается."""
        if key is not None and key == self.held:
            self.held, self.direction = None, 0

    def tick(self) -> None:
        """Таймер: забрать посчитанные в фоне кадры, сделать шаг слайдшоу, обновить заголовок."""
        if self.held is not None and time.monotonic() - self.held_since >= HOLD_DELAY_S:
            self.direction = 1 if self.held == "right" else -1  # стрелку держат — слайдшоу
        self.done = self.preview.collect()
        if self.direction:
            following = self.k + self.direction
            if not 1 <= following <= len(self.preview):
                self.direction = 0
            elif self._ready(following):
                self.go(following)
                return
        if self.waiting and self._ready(self.k):
            self.draw()  # дождались кадра, который считался
        elif self.executor is not None and time.monotonic() - self.title_time > 1.0:
            self._title()  # обновить «посчитано N из M» (не чаще раза в секунду)

    def close(self) -> None:
        """Окно закрывается: остановить таймер и фоновый расчёт."""
        self.timer.stop()
        self.preview.cancel()
        if self.executor is not None:
            self.executor.shutdown(wait=False, cancel_futures=True)

    # --- рисование
    def _ready(self, k: int) -> bool:
        """Можно ли показать кадр k в текущем виде без ожидания."""
        return self.executor is None or self.view not in COMPUTED_VIEWS or self.preview.ready(k)

    def go(self, k: int) -> None:
        """Показать кадр k (в пределах записи)."""
        k = min(max(k, 1), len(self.preview))
        if k != self.k:
            self.k = k
            self.draw()

    def draw(self) -> None:
        """Нарисовать текущий кадр. Номер кадра и вид берутся один раз — подпись всегда про то,
        что нарисовано."""
        k, view = self.k, self.view
        self.shown = (k, view)
        self.done = self.preview.collect()
        self.waiting = not self._ready(k)
        try:
            if self.waiting:
                self.drawer.show(self.preview, k, "frame")
                self.info = "кадр ещё считается…"
            else:
                self.info = self.drawer.show(self.preview, k, view, self.show_matlab)
        except Exception as error:
            log.exception("Ошибка при расчёте кадра")
            self.info = f"ошибка: {error}"
        self._title()

    def _title(self) -> None:
        """Заголовок: запись, кадр, вид; вторая строка — подпись вида и «посчитано N из M»."""
        k, view = self.shown
        n = len(self.preview)
        text = (f"{self.records[self.r].name} ({self.r + 1} из {len(self.records)}) — кадр {k} из {n} "
                f"(файл {self.preview.numbers[k - 1]}) — {VIEWS[view]}")
        details = [self.info] if self.info else []
        if self.executor is not None and self.done < n:
            details.append(f"посчитано {self.done} из {n}")
        if details:
            text += "\n" + " — ".join(details)
        self.ax.set_title(text, fontsize=10, color="black")
        self.title_time = time.monotonic()
        self.fig.canvas.draw_idle()

    def _error(self, text: str) -> None:
        """Показать ошибку красным в заголовке (окно не закрывается)."""
        self.ax.set_title(text, fontsize=10, color="red")
        self.fig.canvas.draw_idle()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    _free_keys()
    viewer = Viewer(settings, thresholds)
    plt.show()
