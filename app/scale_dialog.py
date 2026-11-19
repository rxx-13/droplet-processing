r"""Масштаб по фото: отметить на фотографии отрезок известной длины и получить SCALE_MM_PER_PX.

Окно открывается кнопкой «по фото…» у значения SCALE_MM_PER_PX в панели «Настройки». Показывается
фото (яркость растягивается, тёмные снимки видны). Первый щелчок — начало отрезка; после него через
точку появляются вертикальная и горизонтальная вспомогательные прямые, и вторая точка по умолчанию
ставится на ближайшую из них (отрезок получается строго горизонтальным или вертикальным; Shift —
поставить свободно, например вдоль наклонной линейки). После второго щелчка курсор переходит в поле
длины. Длина вводится в сантиметрах (по умолчанию) или миллиметрах, масштаб = длина в мм / длина
отрезка в px. Полученное значение записывается в SCALE_MM_PER_PX как обычная несохранённая правка
(Ctrl+S — в файл).

pyqtgraph: показ фото, увеличение колесом, сдвиг мышью, щелчок — точка отрезка.
"""

from pathlib import Path
from typing import Any

import numpy as np
import pyqtgraph as pg
from PIL import Image
from PySide6.QtCore import QRectF, Qt
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QVBoxLayout, QWidget)

IMAGE_FILTER = "Картинки (*.jpg *.jpeg *.png *.tif *.tiff *.bmp)"
UNITS = {"см": 10.0, "мм": 1.0}  # сколько миллиметров в единице длины


def mm_per_px(px_length: float, length: float, unit: str = "см") -> float:
    """Масштаб, мм на пиксель: длина length (unit — «см» или «мм») на отрезке px_length пикселей."""
    if px_length <= 0:
        raise ValueError("длина отрезка в пикселях должна быть больше 0")
    return length * UNITS[unit] / px_length


class ScaleDialog(QDialog):
    """Фото с отрезком известной длины. scale — посчитанный масштаб, мм/пиксель (None — не посчитан);
    points — точки отрезка в координатах фото, px (x вправо, y вниз); pixel_length — длина отрезка, px."""

    def __init__(self, image_path: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Масштаб по фото")
        self.resize(1000, 780)
        self.image_path = Path(image_path)
        self.points: list[tuple[float, float]] = []
        self.scale: float | None = None

        with Image.open(self.image_path) as image:
            picture = np.asarray(image.convert("L"), dtype=np.float64)
        self.size = (picture.shape[1], picture.shape[0])  # ширина, высота, px
        low, high = (float(value) for value in np.percentile(picture, (0.5, 99.5)))
        if high <= low:
            high = low + 1.0  # ровное фото: чтобы pyqtgraph не делил на 0

        self.plot = pg.PlotWidget()
        self.plot.setBackground("w")
        self.box = self.plot.getViewBox()
        self.box.setAspectLocked(True)
        self.box.invertY(True)  # ось y вниз, как у фото
        self.box.setMenuEnabled(False)
        self.image = pg.ImageItem()
        self.image.setImage(picture, levels=(low, high), autoLevels=False)
        self.image.setRect(QRectF(0, 0, self.size[0], self.size[1]))
        self.plot.addItem(self.image)
        self.marks = pg.ScatterPlotItem(size=11, symbol="+", pen=pg.mkPen("r", width=1.5))
        self.line = pg.PlotCurveItem(pen=pg.mkPen("r", width=2))
        self.plot.addItem(self.marks)
        self.plot.addItem(self.line)
        pen = pg.mkPen("#d09000", width=1, style=Qt.PenStyle.DashLine)  # вспомогательные прямые
        self.vline = pg.InfiniteLine(angle=90, movable=False, pen=pen)
        self.hline = pg.InfiniteLine(angle=0, movable=False, pen=pen)
        for helper in (self.vline, self.hline):
            self.plot.addItem(helper)
            helper.setVisible(False)
        self.plot.setRange(xRange=(0, self.size[0]), yRange=(self.size[1], 0))
        self.plot.scene().sigMouseClicked.connect(self._clicked)

        self.info = QLabel("Первый щелчок — начало отрезка; вторая точка встаёт на вертикальную или "
                           "горизонтальную прямую через неё (Shift — свободно). Колесо — увеличение, "
                           "перетаскивание — сдвиг.")
        self.info.setWordWrap(True)
        self.length_edit = QLineEdit("1")
        self.length_edit.setToolTip("Сколько миллиметров или сантиметров между двумя точками")
        self.unit = QComboBox()
        self.unit.addItems(list(UNITS))
        self.unit.setCurrentText("см")
        self.reset_button = QPushButton("Сбросить")
        self.result_label = QLabel()
        self.result_label.setWordWrap(True)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Создать")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        row = QHBoxLayout()
        row.addWidget(QLabel("Длина отрезка:"))
        row.addWidget(self.length_edit)
        row.addWidget(self.unit)
        row.addWidget(self.reset_button)
        row.addStretch(1)
        layout = QVBoxLayout(self)
        layout.addWidget(self.plot, 1)
        layout.addWidget(self.info)
        layout.addLayout(row)
        layout.addWidget(self.result_label)
        layout.addWidget(self.buttons)

        self.length_edit.textChanged.connect(self._update)
        self.unit.currentTextChanged.connect(self._update)
        self.reset_button.clicked.connect(lambda checked=False: self.reset())
        self.buttons.accepted.connect(self.accept_scale)
        self.buttons.rejected.connect(self.reject)
        self._update()

    @property
    def pixel_length(self) -> float | None:
        """Длина отмеченного отрезка, px; None — точек ещё не две."""
        if len(self.points) < 2:
            return None
        (x1, y1), (x2, y2) = self.points
        return float(np.hypot(x2 - x1, y2 - y1))

    def _clicked(self, event: Any) -> None:
        """Щелчок по фото: точка отрезка (левая кнопка, в пределах фото); Shift — вторая точка свободно."""
        if event.button() != Qt.MouseButton.LeftButton or event.double():
            return
        if not self.box.sceneBoundingRect().contains(event.scenePos()):
            return
        point = self.box.mapSceneToView(event.scenePos())
        x, y = point.x(), point.y()
        if 0 <= x <= self.size[0] and 0 <= y <= self.size[1]:
            shift = bool(QApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier)
            self.pick_point(x, y, snap=not shift)

    def pick_point(self, x: float, y: float, snap: bool = True) -> None:
        """Добавить точку отрезка, px. Вторая точка при snap ставится на ближайшую вспомогательную
        прямую через первую (x или y первой точки); после неё курсор — в поле длины; третья — новый отрезок."""
        if len(self.points) >= 2:
            self.points = []
        if snap and len(self.points) == 1:
            x1, y1 = self.points[0]
            x, y = (x1, y) if abs(x - x1) <= abs(y - y1) else (x, y1)
        self.points.append((x, y))
        if len(self.points) == 2:
            self.length_edit.setFocus()
            self.length_edit.selectAll()
        self._update()

    def reset(self) -> None:
        """Забыть отрезок и начать заново."""
        self.points = []
        self._update()

    def _length(self) -> float:
        """Длина из поля в единицах unit; 0 — не число или не больше 0."""
        try:
            length = float(self.length_edit.text().strip().replace(",", "."))
        except ValueError:
            return 0.0
        return length if length > 0 else 0.0

    def _update(self) -> None:
        """Нарисовать точки, отрезок и вспомогательные прямые, показать масштаб, включить или выключить
        «Создать»."""
        one = len(self.points) == 1  # после первой точки — прямые, на которые встанет вторая
        if one:
            self.vline.setPos(self.points[0][0])
            self.hline.setPos(self.points[0][1])
        self.vline.setVisible(one)
        self.hline.setVisible(one)
        self.marks.setData([point[0] for point in self.points], [point[1] for point in self.points])
        px_length = self.pixel_length
        self.line.setData([p[0] for p in self.points], [p[1] for p in self.points])
        length = self._length()
        ok = px_length is not None and px_length > 0 and length > 0
        if px_length is None:
            self.result_label.setText("Отрезок не задан.")
        elif not ok:
            self.result_label.setText(f"Отрезок: {px_length:.1f} px. Введите длину больше 0.")
        else:
            value = mm_per_px(px_length, length, self.unit.currentText())
            self.result_label.setText(f"Отрезок: {px_length:.1f} px = {length:g} {self.unit.currentText()} → "
                                f"SCALE_MM_PER_PX = {value:.6g} мм/px")
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(bool(ok))

    def accept_scale(self) -> bool:
        """Посчитать масштаб и закрыть окно с согласием; False — отрезок или длина не заданы."""
        px_length = self.pixel_length
        length = self._length()
        if px_length is None or px_length <= 0 or length <= 0:
            self.result_label.setText("Выберите две точки и введите длину больше 0.")
            return False
        self.scale = mm_per_px(px_length, length, self.unit.currentText())
        self.accept()
        return True
