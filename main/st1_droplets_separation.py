r"""st1 — поиск капель и PIV (порт st1_droplets_separation.m).

Для каждой записи из таблицы:
    1. Фон — кадр BACKGROUND_FRAME; обрабатываются кадры start_frame..fin_frame.
    2. На каждом кадре закрашиваются рим, крупные брызги (площадь больше rim из таблицы) и вода.
    3. На очищенном кадре ищутся капли: центры и радиусы.
    4. Между соседними очищенными кадрами считается PIV, затем фильтруется.
    5. Скорость PIV интерполируется в центры капель (для последнего кадра — PIV предыдущей пары).

Читает: таблицу записей, кадры <IMAGES_DIR>/<путь записи>/.
Пишет в <OUTPUT_DIR>/PIV/<путь записи>_output_st1_droplets/:
    PIV_output_arr.mat           PIV по парам кадров
    PIV_output_filtered_arr.mat  PIV после фильтрации
    particles.mat                капли по кадрам: centers_arr, radii_arr, xd_q_arr, yd_q_arr

Запуск:  .venv\Scripts\python.exe main\st1_droplets_separation.py
Эксперимент выбирается в experiments/current.py, параметры — в его settings.py и thresholds.py.
"""

import sys
from pathlib import Path

sys.dont_write_bytecode = True  # не создавать папки __pycache__
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # чтобы находились папки lib и experiments

import logging  # noqa: E402
from types import ModuleType  # noqa: E402

import numpy as np  # noqa: E402

from experiments.current import settings, thresholds  # noqa: E402
from lib.common import files  # noqa: E402
from lib.common.records import Record, run_stage  # noqa: E402
from lib.st1.droplets import DropletFinder, RimRemover  # noqa: E402
from lib.st1.piv import Piv, filter_piv_by_thresholds  # noqa: E402
from lib.st1.velocity import velocity_at_points  # noqa: E402

log = logging.getLogger("st1")


def process_record(record: Record, settings: ModuleType, thresholds: ModuleType) -> None:
    """st1 для одной записи: шаги 1–5 из описания файла; результат — три .mat в папке st1 записи."""
    t = thresholds
    if record.rim is None:
        raise ValueError("в таблице не задан порог рима (колонка rim / rim_new)")
    frames = files.Frames(record.images_dir(Path(settings.IMAGES_DIR)))
    background = frames.gray(settings.BACKGROUND_FRAME)

    rim_remover = RimRemover.from_thresholds(t, settings.SURFACE_ROW, settings.ERASE_REGIONS)
    finder = DropletFinder.from_thresholds(t)
    piv = Piv.from_thresholds(t, frames.width, frames.height)

    numbers = range(record.start_frame, record.fin_frame + 1)
    centers, radii, piv_raw = [], [], []
    previous = None
    for k, number in enumerate(numbers, start=1):
        log.info("%s: кадр %d из %d (файл %d)", record.name, k, len(numbers), number)
        clean = rim_remover.remove(frames.gray(number), background, record.rim)
        dark = 1.0 - clean  # капли светлые на тёмном — так идёт в PIV
        if previous is not None:
            piv_raw.append(piv.compute(previous, dark))
        previous = dark
        c, r = finder.find(clean)
        centers.append(c)
        radii.append(r)

    piv_filtered = [filter_piv_by_thresholds(p, t) for p in piv_raw]

    xd, yd = [], []
    for k, c in enumerate(centers):
        if c.shape[0] == 0:
            xd.append(np.zeros(0))
            yd.append(np.zeros(0))
            continue
        x, y = velocity_at_points(c, piv_filtered[min(k, len(piv_filtered) - 1)])
        xd.append(x)
        yd.append(y)

    out = record.st1_dir(Path(settings.OUTPUT_DIR))
    out.mkdir(parents=True, exist_ok=True)
    # ячейки PIV длиной в число кадров: последняя пустая, как в MATLAB
    files.save_mat(out / "PIV_output_arr.mat",
                   PIV_output_arr=files.to_cell([p.to_struct() for p in piv_raw] + [None]))
    files.save_mat(out / "PIV_output_filtered_arr.mat",
                   PIV_output_filtered_arr=files.to_cell([p.to_struct() for p in piv_filtered] + [None]))
    files.save_mat(out / "particles.mat",
                   centers_arr=files.to_cell(centers),
                   radii_arr=files.to_cell([r.reshape(-1, 1) for r in radii]),
                   xd_q_arr=files.to_cell([a.reshape(-1, 1) for a in xd]),
                   yd_q_arr=files.to_cell([a.reshape(-1, 1) for a in yd]))
    log.info("%s: капель всего %d, результат в %s", record.name, sum(len(r) for r in radii), out)


if __name__ == "__main__":
    sys.exit(run_stage("st1", settings, thresholds, process_record))
