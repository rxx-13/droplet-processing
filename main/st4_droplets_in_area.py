r"""st4 — капли в областях поиска (порт st4_202210_droplets_in_area.m).

Для каждой записи из таблицы и каждой области поиска (все сочетания AREA_HEIGHTS × AREA_LOW_EDGES):
    1. Отбирает траектории из результата st2, задевающие область.
    2. По каждой такой траектории — среднее положение, скорость и радиус её точек в области.
    3. Переводит в мм, м/с, мкм (начало координат — cx, cy из таблицы, ось y вверх).
    4. Сохраняет по файлу на область и общую таблицу по всем областям.

Читает: таблицу записей, <OUTPUT_DIR>/PTV/<путь записи>_output_st2_PTV/ptv_unfiltered.mat.
Пишет в <OUTPUT_DIR>/st4/<путь записи>_output_st4/:
    <имя области>.dat   по строке на траекторию: x мм, y мм, vx м/с, vy м/с, r мкм (нет траекторий — «0»)
    <имя области>.mat   то же + в px, границы области (только если траектории есть)
    <имя области>.png   картинка области (только если траектории есть)
    _all_stat.dat       по строке на область: высота мм, нижний край мм, число траекторий, средние vx, vy, r

Запуск:  .venv\Scripts\python.exe main\st4_droplets_in_area.py
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
from lib.st4.areas import (Area, area_file_name, plot_area, px_per_frame_to_m_per_s,  # noqa: E402
                           px_to_mm, tracks_in_area)

log = logging.getLogger("st4")


def process_record(record: Record, settings: ModuleType, thresholds: ModuleType) -> None:
    """st4 для одной записи: траектории из ptv_unfiltered.mat -> файлы по каждой области и _all_stat.dat."""
    if record.cx is None or record.cy is None:
        raise ValueError("в таблице не задано начало координат (колонки cx/c_x, cy/c_y)")
    scale, fps, cx, cy = settings.SCALE_MM_PER_PX, settings.FPS, record.cx, record.cy
    output = Path(settings.OUTPUT_DIR)
    tracks = files.load_cell(record.st2_dir(output) / "ptv_unfiltered.mat", "p_arr")
    out = record.st4_dir(output)
    out.mkdir(parents=True, exist_ok=True)

    summary = []  # по строке на область, где есть траектории
    for height in settings.AREA_HEIGHTS:
        for low_edge in settings.AREA_LOW_EDGES:
            area = Area(x=(float(settings.AREA_X[0]), float(settings.AREA_X[1])),
                        y=(float(low_edge - height), float(low_edge)))
            height_mm = scale * height
            low_edge_mm = float(px_to_mm([[np.nan, low_edge]], scale, cx, cy)[0, 1])
            name = area_file_name(height_mm, low_edge_mm)

            means, inside = tracks_in_area(tracks, area)
            if means.shape[0] == 0:
                files.save_dat(out / f"{name}.dat", 0.0)
                continue
            physical = np.column_stack([px_to_mm(means[:, 0:2], scale, cx, cy),  # x, y мм
                                        px_per_frame_to_m_per_s(means[:, 2:4], scale, fps),  # vx, vy м/с
                                        scale * 1000 * means[:, 4]])  # r мкм
            summary.append([height_mm, low_edge_mm, means.shape[0],
                            physical[:, 2].mean(), physical[:, 3].mean(), physical[:, 4].mean()])

            plot_area(out / f"{name}.png", tracks, inside, means, area, scale, cx, cy,
                      thresholds.AREA_PLOT_ARROW_SCALE, thresholds.AREA_PLOT_Y_LIMITS_MM)
            files.save_mat(out / f"{name}.mat", p_in_area=means, p_in_area_scale=physical,
                           search_area_x=np.array([area.x]), search_area_y=np.array([area.y]),
                           area_low_edge_hight=float(low_edge), area_hight=float(height),
                           area_low_edge_hight_scale_mm=low_edge_mm, area_hight_scale_mm=height_mm)
            files.save_dat(out / f"{name}.dat", physical)

    # Как в MATLAB: области без траекторий в общую таблицу не попадают; если их нет вовсе — файл пустой.
    if summary:
        files.save_dat(out / "_all_stat.dat", np.array(summary))
    else:
        (out / "_all_stat.dat").write_bytes(b"")
    log.info("%s: областей с траекториями %d, результат в %s", record.name, len(summary), out)


if __name__ == "__main__":
    sys.exit(run_stage("st4", settings, thresholds, process_record))
