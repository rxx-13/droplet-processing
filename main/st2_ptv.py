r"""st2 — PTV-трекинг: связывание капель соседних кадров в траектории (порт st2_PTV_mod2.m).

Для каждой записи из таблицы:
    1. Читает капли по кадрам из результата st1 (particles.mat).
    2. Строит траектории, идя от конца записи к началу (алгоритм — в lib/st2/tracking.py).
    3. Сохраняет траектории и картинку.

Читает: таблицу записей, <OUTPUT_DIR>/PIV/<путь записи>_output_st1_droplets/particles.mat.
Пишет в <OUTPUT_DIR>/PTV/<путь записи>_output_st2_PTV/:
    ptv_unfiltered.mat  траектории p_arr (каждая — матрица k×16, описание колонок в lib/st2/tracking.py)
    tracks.png          траектории: длиннее TRACKS_PLOT_MIN_POINTS точек — синие, короче — оранжевые;
                        красные точки — капли, не вошедшие ни в одну траекторию

Запуск:  .venv\Scripts\python.exe main\st2_ptv.py
Эксперимент выбирается в experiments/current.py, параметры — в его settings.py и thresholds.py.
"""

import sys
from pathlib import Path

sys.dont_write_bytecode = True  # не создавать папки __pycache__
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # чтобы находились папки lib и experiments

import logging  # noqa: E402
from types import ModuleType  # noqa: E402

from experiments.current import settings, thresholds  # noqa: E402
from lib.common.files import load_particles  # noqa: E402
from lib.common.records import Record, run_stage  # noqa: E402
from lib.st2.tracking import Tracker, plot_tracks, save_tracks  # noqa: E402

log = logging.getLogger("st2")


def process_record(record: Record, settings: ModuleType, thresholds: ModuleType) -> None:
    """st2 для одной записи: капли из particles.mat -> траектории -> ptv_unfiltered.mat и tracks.png."""
    t = thresholds
    output = Path(settings.OUTPUT_DIR)
    centers, radii, xd, yd = load_particles(record.st1_dir(output))

    tracker = Tracker(t.PTV_SEARCH_RADIUS, t.PTV_MAX_RADIUS_RATIO, t.PTV_MAX_FRAME_GAP,
                      t.PTV_MAX_DEVIATION, t.PTV_MIN_STRAIGHTNESS, t.PTV_PIV_WEIGHT, t.PTV_POLY_POINTS)
    tracks = tracker.track(centers, radii, xd, yd)

    out = record.st2_dir(output)
    out.mkdir(parents=True, exist_ok=True)
    plot_tracks(out / "tracks.png", tracks, t.TRACKS_PLOT_MIN_POINTS)
    save_tracks(out, tracks)
    log.info("%s: траекторий %d, результат в %s", record.name, len(tracks), out)


if __name__ == "__main__":
    sys.exit(run_stage("st2", settings, thresholds, process_record))
