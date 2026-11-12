r"""Пересчёт стадий для списка экспериментов — в параллельных процессах, без приложения.

    .venv\Scripts\python.exe tools\recalc.py cw5_F25 cw5_F29 cw5_F33 [--stages st1,st2,st4] [--workers N]

Каждая запись — в своём процессе: используется тот же механизм, что у вкладки «Запуск» приложения
(lib/common/jobs.py), поэтому скорость та же — одновременно не больше workers записей (по умолчанию
все ядра, кроме одного). Все эксперименты идут одной очередью; ошибка в записи не останавливает остальные.

Печатает ход работы (начало и конец каждой стадии, итог по каждой записи) и в конце — сколько записей
готово и список ошибок. Старые результаты перезаписываются.
"""

import argparse
import os
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True  # не создавать папки __pycache__
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # чтобы находились lib и experiments

from lib.common.jobs import STAGES, Job, JobRunner, load_module  # noqa: E402
from lib.common.records import read_records  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def ready(record) -> bool:
    """Готова ли запись ко всем стадиям: есть диапазон кадров, rim и центр."""
    return (record.has_frames and record.rim is not None
            and record.cx is not None and record.cy is not None)


def build_jobs(names: list[str], stages: tuple[str, ...]) -> tuple[list[Job], list[str]]:
    """Задания на готовые записи экспериментов names; возвращает задания и ошибки чтения настроек/таблиц."""
    jobs: list[Job] = []
    errors: list[str] = []
    job_id = 1
    for name in names:
        folder = ROOT / "experiments" / name
        settings_file, thresholds_file = folder / "settings.py", folder / "thresholds.py"
        try:
            settings = load_module(settings_file, f"recalc_{name}")
            records = read_records(settings.TABLE_FILE, settings.CONDITIONS, settings.RECORDS)
        except Exception as error:  # нет папки, таблицы или ошибка в settings.py — остальные считаем
            errors.append(f"{name}: {type(error).__name__}: {error}")
            print(f"{name}: ОШИБКА чтения настроек/таблицы: {type(error).__name__}: {error}", flush=True)
            continue
        good = [record for record in records if ready(record)]
        skipped = [record.name for record in records if not ready(record)]
        note = f", пропущено {len(skipped)}" if skipped else ""
        print(f"{name}: {len(good)} записей{note}", flush=True)
        for record in good:
            jobs.append(Job(job_id, record, stages, str(settings_file), str(thresholds_file)))
            job_id += 1
    return jobs, errors


def main(argv: list[str] | None = None) -> int:
    """Разобрать аргументы, посчитать все задания и напечатать ход работы и итог."""
    parser = argparse.ArgumentParser(description="Пересчёт стадий для списка экспериментов.")
    parser.add_argument("experiments", nargs="+", help="имена папок в experiments/")
    parser.add_argument("--stages", default=",".join(STAGES), help="стадии через запятую (по умолчанию st1,st2,st4)")
    parser.add_argument("--workers", type=int, default=0, help="сколько записей одновременно (0 — все ядра, кроме одного)")
    args = parser.parse_args(argv)
    requested = {name.strip() for name in args.stages.split(",") if name.strip()}
    unknown = sorted(requested - set(STAGES))
    if unknown:
        parser.error(f"неизвестные стадии: {', '.join(unknown)}; есть {', '.join(STAGES)}")
    if not requested:
        parser.error("не заданы стадии")
    stages = tuple(stage for stage in STAGES if stage in requested)  # всегда по порядку st1 → st2 → st4

    jobs, errors = build_jobs(args.experiments, stages)
    cpus = os.cpu_count() or 1
    workers = min(args.workers or max(1, cpus - 1), cpus)  # не больше числа ядер
    print(f"Всего записей: {len(jobs)}, стадии: {', '.join(stages)}, одновременно процессов: {workers}", flush=True)
    if not jobs:
        return 1 if errors else 0

    runner = JobRunner(workers)
    runner.submit(jobs)
    started = time.time()
    done_stages = 0
    finished: set[int] = set()
    logs: dict[int, list[str]] = {}
    last_report = 0.0
    while True:
        messages = runner.poll()
        for job_id, kind, stage, text in messages:
            job = runner.jobs[job_id]
            label = f"{Path(job.settings_file).parent.name}/{job.record.name}"
            if kind == "log":
                logs.setdefault(job_id, []).append(text)
                logs[job_id] = logs[job_id][-15:]
            elif kind == "start":
                print(f"{label}: {stage}...", flush=True)
            elif kind == "done":
                done_stages += 1
                if stage == stages[-1]:
                    finished.add(job_id)
                print(f"{label}: {stage} ✓", flush=True)
            elif kind == "error":
                errors.append(f"{label}: {stage}: {text}")
                print(f"{label}: ОШИБКА {stage}: {text}", flush=True)
                for line in logs.pop(job_id, []):
                    print(f"    {line}", flush=True)
        if not runner.busy() and not messages:
            break
        elapsed = time.time() - started
        if elapsed - last_report >= 30:
            last_report = elapsed
            print(f"... {len(finished)} из {len(jobs)} записей готовы, "
                  f"{done_stages} стадий, ошибок {len(errors)}, прошло {elapsed / 60:.1f} мин", flush=True)
        time.sleep(0.2)
    elapsed = time.time() - started
    print(f"ГОТОВО: {len(finished)} из {len(jobs)} записей, {done_stages} стадий, "
          f"ошибок {len(errors)}, время {elapsed / 60:.1f} мин", flush=True)
    for line in errors:
        print(f"ОШИБКА: {line}", flush=True)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
