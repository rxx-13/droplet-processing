r"""Сравнение результатов Python с результатами MATLAB для текущего эксперимента.

Это не тест «прошёл / не прошёл», а отчёт: для каждой записи и стадии печатает, насколько
результаты совпадают. Сравниваются записи, выбранные в settings (CONDITIONS, RECORDS), у которых
есть и результат Python (OUTPUT_DIR), и результат MATLAB (MATLAB_RESULTS_DIR в settings;
структура папок та же: PIV/, PTV/, st4/).

    st1  particles.mat: на скольких кадрах совпало число капель; на них — наибольшие расхождения
         центров, радиусов и скоростей PIV в центрах капель.
    st2  ptv_unfiltered.mat: число траекторий; сколько совпали полностью (до 1e-9).
    st4  .dat: сколько файлов совпали побайтно; наибольшее относительное расхождение в остальных.

Запуск:  .venv\Scripts\python.exe tests\compare_with_matlab.py
"""

import sys
from pathlib import Path

sys.dont_write_bytecode = True  # не создавать папки __pycache__
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # чтобы находились папки lib и experiments

from types import ModuleType  # noqa: E402

import numpy as np  # noqa: E402

from experiments.current import settings  # noqa: E402
from lib.common import files  # noqa: E402
from lib.common.files import load_particles  # noqa: E402
from lib.common.records import Record, read_records  # noqa: E402


def _max_abs(a: np.ndarray, b: np.ndarray) -> float:
    d = np.abs(a - b)
    d = d[~np.isnan(d)]
    return float(d.max()) if d.size else 0.0


def compare_st1(py_dir: Path, matlab_dir: Path) -> str:
    c1, r1, x1, y1 = load_particles(py_dir)
    c2, r2, x2, y2 = load_particles(matlab_dir)
    if len(c1) != len(c2):
        return f"разное число кадров: {len(c1)} и {len(c2)} в MATLAB"
    same_count = [k for k in range(len(c1)) if c1[k].shape[0] == c2[k].shape[0]]
    other = [k + 1 for k in range(len(c1)) if k not in same_count]
    dc = max((_max_abs(c1[k], c2[k]) for k in same_count), default=0.0)
    dr = max((_max_abs(r1[k], r2[k]) for k in same_count), default=0.0)
    dv = max((max(_max_abs(x1[k], x2[k]), _max_abs(y1[k], y2[k])) for k in same_count), default=0.0)
    nan_diff = sum(int(np.sum(np.isnan(x1[k]) != np.isnan(x2[k]))) for k in same_count)
    droplets = sum(c.shape[0] for c in c1), sum(c.shape[0] for c in c2)
    text = (f"капель {droplets[0]} (MATLAB {droplets[1]}); число капель совпало на {len(same_count)} "
            f"из {len(c1)} кадров; max|Δ| центров {dc:.2g} px, радиусов {dr:.2g} px, "
            f"скоростей {dv:.2g} px/кадр; NaN-скорости не совпали у {nan_diff}")
    if len(other) > 10:
        text += f"; другое число капель на {len(other)} кадрах"
    elif other:
        text += f"; кадры с другим числом капель: {other}"
    return text


def compare_st2(py_dir: Path, matlab_dir: Path) -> str:
    t1 = files.load_cell(py_dir / "ptv_unfiltered.mat", "p_arr")
    t2 = files.load_cell(matlab_dir / "ptv_unfiltered.mat", "p_arr")
    identical = sum(1 for a, b in zip(t1, t2)
                    if a.shape == b.shape and np.allclose(a, b, rtol=1e-9, atol=1e-9, equal_nan=True))
    return f"траекторий {len(t1)} (MATLAB {len(t2)}); совпали полностью {identical}"


def compare_st4(py_dir: Path, matlab_dir: Path) -> str:
    names = sorted(p.name for p in matlab_dir.glob("*.dat"))
    missing = [n for n in names if not (py_dir / n).exists()]
    identical, other_rows, close, worst = 0, 0, 0, 0.0
    for name in names:
        if name in missing:
            continue
        if (py_dir / name).read_bytes() == (matlab_dir / name).read_bytes():
            identical += 1
            continue
        a, b = files.load_dat(py_dir / name), files.load_dat(matlab_dir / name)
        if a.shape != b.shape:  # другое число траекторий (строк)
            other_rows += 1
            continue
        close += 1
        with np.errstate(divide="ignore", invalid="ignore"):
            rel = np.abs(a - b) / np.maximum(np.abs(b), 1e-300)
        worst = max(worst, float(np.nanmax(rel)) if rel.size else 0.0)
    text = f"файлов .dat {len(names)}; совпали побайтно {identical}"
    if close:
        text += f"; с тем же числом строк, но другими числами {close} (до {worst:.2%})"
    if other_rows:
        text += f"; с другим числом строк {other_rows}"
    return text + (f"; нет в Python: {len(missing)}" if missing else "")


def compare_record(record: Record, settings: ModuleType) -> None:
    py_root, matlab_root = Path(settings.OUTPUT_DIR), Path(settings.MATLAB_RESULTS_DIR)
    print(f"\n{record.name}")
    stages = [("st1", record.st1_dir, "particles.mat", compare_st1),
              ("st2", record.st2_dir, "ptv_unfiltered.mat", compare_st2),
              ("st4", record.st4_dir, "_all_stat.dat", compare_st4)]
    for stage, folder, marker, compare in stages:
        py_dir, matlab_dir = folder(py_root), folder(matlab_root)
        if not (py_dir / marker).exists() or not (matlab_dir / marker).exists():
            which = "Python" if not (py_dir / marker).exists() else "MATLAB"
            print(f"  {stage}: нет результата {which}")
            continue
        print(f"  {stage}: {compare(py_dir, matlab_dir)}")


if __name__ == "__main__":
    if not getattr(settings, "MATLAB_RESULTS_DIR", None):
        sys.exit("В settings не задан MATLAB_RESULTS_DIR — сравнивать не с чем")
    records = [r for r in read_records(Path(settings.TABLE_FILE), settings.CONDITIONS, settings.RECORDS)
               if r.has_frames]
    for r in records:
        compare_record(r, settings)
