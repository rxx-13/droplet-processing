"""Чтение и запись файлов: кадры записи, .mat (формат MATLAB), .dat (текст, как MATLAB save -ascii).

Кадры. В папке записи берутся все картинки одного формата (jpg, png, tif, bmp) и сортируются
по последнему числу в имени файла (как ExtractNumber в MATLAB): «img_2.jpg» раньше «img_10.jpg».
Номера кадров — с 1, как в таблице записей (start_frame, fin_frame).

.mat. Списки (кадры, траектории) хранятся как ячейки MATLAB N×1; пустой элемент — [].

.dat. Каждое число в формате %16.7e, строки заканчиваются CRLF — побайтно как у MATLAB.
"""

import math
import re
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
from scipy.io import loadmat, savemat

IMAGE_FORMATS = {".jpg": "jpg", ".jpeg": "jpg", ".png": "png", ".tif": "tif", ".tiff": "tif", ".bmp": "bmp"}


# ------------------------------------------------------------------ кадры


def last_number_in_name(name: str) -> float:
    """Последняя группа цифр в имени файла (NaN, если цифр нет)."""
    numbers = re.findall(r"\d+", name)
    return float(int(numbers[-1])) if numbers else math.nan


class Frames:
    """Кадры одной записи, отсортированные по номеру в имени файла (аналог imLoader_anyext в MATLAB).

    folder — папка записи (Record.images_dir). Ошибка, если папки нет, в ней нет картинок или картинки
    разных форматов. width, height — размер кадра, px; paths — файлы кадров по порядку.
    """

    def __init__(self, folder: Path) -> None:
        if not folder.is_dir():
            raise FileNotFoundError(f"Нет папки с кадрами: {folder}")
        images = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_FORMATS]
        if not images:
            raise FileNotFoundError(f"В папке нет картинок: {folder}")
        formats = {IMAGE_FORMATS[p.suffix.lower()] for p in images}
        if len(formats) > 1:
            raise ValueError(f"В папке картинки разных форматов ({', '.join(sorted(formats))}): {folder}")

        def sort_key(path: Path) -> tuple[bool, float]:
            n = last_number_in_name(path.name)
            return (math.isnan(n), 0.0 if math.isnan(n) else n)  # файлы без номера — в конец

        self.paths = sorted(images, key=sort_key)
        with Image.open(self.paths[0]) as im:
            self.width, self.height = im.size

    def __len__(self) -> int:
        return len(self.paths)

    def gray(self, number: int) -> np.ndarray:
        """Кадр №number (с 1) как массив float64 (строки × столбцы); у цветных — первый канал."""
        if not 1 <= number <= len(self.paths):
            raise IndexError(f"Кадр {number} вне диапазона 1..{len(self.paths)}")
        with Image.open(self.paths[number - 1]) as im:
            pixels = np.asarray(im)
        if pixels.ndim == 3:
            pixels = pixels[:, :, 0]
        return pixels.astype(np.float64)


# ------------------------------------------------------------------ .mat


def to_cell(items: list[Any]) -> np.ndarray:
    """Список -> ячейка MATLAB N×1; None и пустые массивы -> []."""
    cell = np.empty((len(items), 1), dtype=object)
    for i, item in enumerate(items):
        empty = item is None or (isinstance(item, np.ndarray) and item.size == 0)
        cell[i, 0] = np.zeros((0, 0)) if empty else item
    return cell


def load_cell(path: Path, name: str) -> list[Any]:
    """Читает ячейку MATLAB как список; числовые массивы приводятся к float64
    (MATLAB хранит целые числа как uint8/uint16)."""
    cell = loadmat(path, variable_names=[name], squeeze_me=False)[name]
    out: list[Any] = []
    for item in cell.reshape(-1, order="F"):
        numeric = isinstance(item, np.ndarray) and item.dtype != object and item.dtype.names is None
        out.append(item.astype(np.float64) if numeric else item)
    return out


def save_mat(path: Path, **variables: Any) -> None:
    """Сохраняет переменные в .mat (формат MATLAB 5, сжатый): save_mat(путь, имя=значение, ...)."""
    savemat(path, variables, format="5", do_compression=True)


def load_particles(st1_dir: Path) -> tuple[list[np.ndarray], list[np.ndarray], list[np.ndarray], list[np.ndarray]]:
    """Капли из particles.mat (результат st1) по кадрам: центры (N, 2: x, y; px с 1), радиусы,
    скорости PIV xd, yd (N,), px/кадр."""
    path = st1_dir / "particles.mat"

    def column(name: str, width: int) -> list[np.ndarray]:
        return [a.reshape(a.shape[0], -1) if a.size else np.zeros((0, width)) for a in load_cell(path, name)]

    centers = column("centers_arr", 2)
    radii, xd, yd = ([a.ravel() for a in column(name, 1)] for name in ("radii_arr", "xd_q_arr", "yd_q_arr"))
    return centers, radii, xd, yd


# ------------------------------------------------------------------ .dat


def _dat_number(v: float) -> str:
    """Число в 16 символов, как пишет MATLAB save -ascii (%16.7e; NaN и Inf — словами)."""
    if math.isnan(v):
        return "             NaN"
    if math.isinf(v):
        return "             Inf" if v > 0 else "            -Inf"
    return f"{v:16.7e}"


def save_dat(path: Path, data: np.ndarray | float) -> None:
    """Таблица (или одно число) в текстовый .dat побайтно как MATLAB save(..., '-ascii'): строки CRLF."""
    table = np.atleast_2d(np.asarray(data, dtype=np.float64))
    lines = ("".join(_dat_number(float(v)) for v in row) + "\r\n" for row in table)
    path.write_bytes("".join(lines).encode("ascii"))


def load_dat(path: Path) -> np.ndarray:
    """Читает .dat как таблицу (пустой файл -> массив 0×0)."""
    if not path.read_text().strip():
        return np.zeros((0, 0))
    return np.atleast_2d(np.loadtxt(path))
