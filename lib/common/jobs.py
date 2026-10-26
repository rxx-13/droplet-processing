"""Запуск стадий для приложения: каждая запись — в отдельном процессе, ход работы — в очередь сообщений.

Стадия выполняется той же функцией process_record, что и скрипт стадии в main/ (скрипты не меняются и не
дублируются); настройки и пороги процесс читает из файлов settings.py и thresholds.py — из сохранённых.
Задание (Job) — запись и цепочка стадий, например st1 → st2 → st4: стадии идут по порядку, ошибка
останавливает цепочку этой записи (другие записи продолжают). JobRunner держит очередь заданий и запускает
одновременно не больше workers процессов; stop останавливает всё сразу (процессы завершаются, поэтому
прерванная стадия может оставить неполные файлы результатов).

Сообщения из процессов — кортежи (номер задания, вид, стадия, текст). Вид: "start" — стадия началась;
"log" — строка хода работы (то, что скрипт стадии пишет в консоль); "done" — стадия готова; "error" —
ошибка (текст — что случилось); "stopped" — задание остановлено (или не запускалось).
"""

import importlib.util
import logging
import multiprocessing
import queue as queue_module
import traceback
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from .records import Record

ROOT = Path(__file__).resolve().parents[2]
STAGE_SCRIPTS = {"st1": "st1_droplets_separation", "st2": "st2_ptv", "st4": "st4_droplets_in_area"}  # main/*.py
STAGES = tuple(STAGE_SCRIPTS)


@dataclass(frozen=True)
class Job:
    """Задание: id — номер; record — запись; stages — стадии по порядку ("st1", "st2", "st4");
    settings_file, thresholds_file — файлы настроек и порогов эксперимента."""

    id: int
    record: Record
    stages: tuple[str, ...]
    settings_file: str
    thresholds_file: str


def load_module(path: Path, name: str) -> ModuleType:
    """Модуль из файла .py (настройки, пороги, скрипт стадии)."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _QueueLog(logging.Handler):
    """Строки хода работы стадии (logging) -> очередь сообщений приложения."""

    def __init__(self, messages: Any, job_id: int) -> None:
        super().__init__(logging.INFO)
        self.messages, self.job_id, self.stage = messages, job_id, ""
        self.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        """Отправить строку в очередь."""
        self.messages.put((self.job_id, "log", self.stage, self.format(record)))


def run_job(job: Job, messages: Any) -> None:
    """Выполнить стадии задания по порядку (в отдельном процессе); ход работы и итог — в очередь messages."""
    log = _QueueLog(messages, job.id)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(log)
    for stage in job.stages:
        log.stage = stage
        messages.put((job.id, "start", stage, ""))
        try:
            settings = load_module(Path(job.settings_file), "job_settings")
            thresholds = load_module(Path(job.thresholds_file), "job_thresholds")
            script = load_module(ROOT / "main" / f"{STAGE_SCRIPTS[stage]}.py", STAGE_SCRIPTS[stage])
            script.process_record(job.record, settings, thresholds)
        except Exception as error:  # ошибка записи — в сообщение, цепочка этой записи останавливается
            for line in traceback.format_exc().splitlines():
                messages.put((job.id, "log", stage, line))
            messages.put((job.id, "error", stage, f"{type(error).__name__}: {error}"))
            return
        messages.put((job.id, "done", stage, ""))


class JobRunner:
    """Очередь заданий: каждое — в своём процессе, одновременно не больше workers.

    submit — поставить задания; poll — запустить ожидающие (если есть место) и забрать сообщения (вызывать
    по таймеру); stop — остановить всё; busy — идёт ли работа.
    """

    def __init__(self, workers: int) -> None:
        self.workers = max(1, workers)
        self.context = multiprocessing.get_context("spawn")
        self.messages = self.context.Queue()
        self.jobs: dict[int, Job] = {}
        self.waiting: deque[Job] = deque()
        self.running: dict[int, Any] = {}  # номер задания -> процесс
        self.ended: set[int] = set()  # задания, приславшие итог (последняя стадия готова или ошибка)

    def submit(self, jobs: list[Job]) -> None:
        """Поставить задания в очередь (запустятся при следующем poll)."""
        for job in jobs:
            self.jobs[job.id] = job
            self.waiting.append(job)

    def busy(self) -> bool:
        """Есть ли ожидающие или идущие задания."""
        return bool(self.waiting or self.running)

    def _drain(self) -> list[tuple[int, str, str, str]]:
        """Все сообщения, уже пришедшие из процессов."""
        found = []
        while True:
            try:
                found.append(self.messages.get_nowait())
            except queue_module.Empty:
                return found

    def poll(self) -> list[tuple[int, str, str, str]]:
        """Запустить ожидающие задания, если есть свободные места, и забрать сообщения процессов."""
        dead = [job_id for job_id, process in self.running.items() if not process.is_alive()]
        found = self._drain()  # после проверки: сообщения завершившихся процессов уже в очереди
        for job_id, kind, stage, _ in found:
            if kind == "error" or (kind == "done" and stage == self.jobs[job_id].stages[-1]):
                self.ended.add(job_id)
        for job_id in dead:
            process = self.running.pop(job_id)
            process.join()
            if job_id not in self.ended:  # процесс упал, не прислав итога
                found.append((job_id, "error", "", f"процесс обработки завершился с кодом {process.exitcode}"))
                self.ended.add(job_id)
        while self.waiting and len(self.running) < self.workers:
            job = self.waiting.popleft()
            process = self.context.Process(target=run_job, args=(job, self.messages), daemon=True)
            process.start()
            self.running[job.id] = process
        return found

    def stop(self) -> list[tuple[int, str, str, str]]:
        """Остановить всё: ожидающие задания убрать, идущие процессы завершить. Возвращает сообщения
        об остановке (и всё, что успело прийти до неё)."""
        found = self._drain()
        found += [(job.id, "stopped", "", "не запускалось") for job in self.waiting]
        self.waiting.clear()
        for process in self.running.values():
            process.terminate()
        for job_id, process in self.running.items():
            process.join(timeout=10)
            found.append((job_id, "stopped", "", "остановлено"))
            self.ended.add(job_id)
        self.running.clear()
        self.messages = self.context.Queue()  # прерванный процесс мог испортить очередь — дальше новая
        return found
