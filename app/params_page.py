"""Панель «Настройки»: все значения settings.py и thresholds.py эксперимента — полями ввода и ползунками.

Панель стоит на вкладке «Записи» справа от кадра (вторая вкладка рядом с «Таблицей»), чтобы пороги
подбирались «на глаз»: правка значения через короткую паузу пересчитывает кадр. Поля идут по порядку:
общие настройки (файлы, записи, съёмка), затем st1 → st2 → st4 → графики — независимо от того, в каком
файле значение (SECTION_ORDER). Подпись поля — имя и комментарий из файла (пояснение над строкой — во
всплывающей подсказке), под полем — практическое пояснение HINTS: что значение делает и в какую сторону
его менять, если на кадре что-то не так (поиск учитывает и его). У чисел и кортежей из чисел
(например, RIM_GRAY_RANGE) — ползунок на каждое число: от 0 (у отрицательных — от удвоенного значения)
до удвоенного значения; набранное в поле число становится новой серединой ползунка. Строки (пути) вводятся
без кавычек; кортежи и списки — как в файле, например (-100, 0) или [10, 20].

«Сохранить настройки» записывает в файлы только изменённые значения (комментарии и остальное остаются);
«Вернуть как в файлах» — забыть правки. Пока правки не сохранены, скрипты main/ работают со значениями
из файлов. Ctrl+Z / Ctrl+Y приложения отменяют и повторяют правки значений по шагам: правки одного
значения подряд (набор, ползунок) — один шаг; шаги идут в общую историю (app/undo.py). У SURFACE_ROW —
кнопка «по кадру» (pick_requested): RecordsPage ждёт щелчок по кадру и пишет значение в settings.py или
в колонку таблицы, имя которой записано в SURFACE_ROW. Логика без окон — lib/common/config_edit.py.
"""

import html
import math
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea, QSlider,
                               QToolButton, QVBoxLayout, QWidget)

from lib.common.config_edit import Setting, SettingsFile, literal, value_text

FLOAT_STEPS = 1000  # число шагов ползунка дробного числа
MAX_SLIDERS = 4  # ползунки — у кортежей не длиннее этого
FILE_TITLES = {"settings": "settings.py", "thresholds": "thresholds.py"}  # заголовок, если разделов нет
CHANGED_STYLE = "QLineEdit { background: #fff1b8; }"  # несохранённая правка
INVALID_STYLE = "QLineEdit { background: #ffc9c9; }"  # ошибка ввода

# Порядок разделов в панели: общие настройки, затем st1 → st2 → st4 → графики — независимо от того,
# в каком файле значение. Неизвестные разделы — в конце, в порядке файлов.
SECTION_ORDER = (
    "файлы",
    "какие записи обрабатывать",
    "съёмка",
    "st1: удаление рима",
    "st1: поиск капель",
    "st1: piv",
    "st2:",
    "st4:",
    "графики",
    "необязательно",
)

# Практические пояснения к значениям: что меняется на кадре и в какую сторону крутить, если на картинке
# что-то не так. Показываются под полем (и участвуют в поиске); сами значения эти тексты не меняют.
HINTS = {
    "IMAGES_DIR": "Где лежат кадры: в этой папке — подпапка каждой записи (images_path_side из таблицы). "
                  "Ошибка здесь — «нет папки с кадрами».",
    "TABLE_FILE": "Таблица записей xlsx: кадры, диапазон, rim, c_x/c_y. Её правят на вкладке «Записи».",
    "OUTPUT_DIR": "Куда писать результаты (PIV, PTV, st4). На расчёт не влияет; старые результаты этой "
                  "папки перезаписываются.",
    "CONDITIONS": "Обрабатывать только записи с этими номерами cond_num. Пусто — все записи.",
    "RECORDS": "Обрабатывать только эти записи rec_name. Пусто — все записи.",
    "SCALE_MM_PER_PX": "Масштаб съёмки, мм на пиксель. От него зависят все размеры в мм и скорости в м/с: "
                       "ошибка в 2 раза — во столько же раз неверны r и v.",
    "FPS": "Частота съёмки. От неё зависят скорости в м/с (px/кадр × FPS); на размеры в мм и px не влияет.",
    "SURFACE_ROW": "Строка кадра (с 1), ниже которой всё считается водой и закрашивается. Больше (ниже) — "
                   "вода закрашивается ниже, капли у поверхности остаются; меньше (выше) — срезаются "
                   "настоящие капли. Ставится кнопкой «по кадру».",
    "BACKGROUND_FRAME": "Номер файла записи без капель — фон. Если взят кадр с каплями, они попадут в маску "
                        "рима и пропадут; лучше кадр до появления капель.",
    "ERASE_REGIONS": "Прямоугольники, которые закрашиваются вручную (блики, посторонние предметы). "
                     "Влияет только на то, что стирается как рим.",
    "AREA_X": "Области st4: левая и правая границы, px. Влияет только на статистику st4, не на поиск капель.",
    "AREA_HEIGHTS": "Области st4: высоты, px. Больше высот — больше строк в статистике st4.",
    "AREA_LOW_EDGES": "Области st4: положения нижнего края, px. Вместе с AREA_HEIGHTS задают сетку областей.",
    "MATLAB_RESULTS_DIR": "Результаты MATLAB для сравнения (tests/compare_with_matlab.py и «Обработка "
                          "результатов»). На расчёт Python не влияет.",
    "RIM_GRAY_RANGE": "Какие перепады яркости «кадр минус фон» считать тёмными; диапазон растягивается "
                      "в 0…1. Нижнее число ближе к 0 — «тёмными» становятся более светлые пятна: "
                      "закрашивается больше (рим, брызги, блики, иногда капли у бэга); дальше от 0 — "
                      "только более тёмное.",
    "RIM_BINARIZE_THRESHOLD": "Порог «тёмного» после растяжения. Меньше — закрашивается больше "
                              "(сиреневое пятно растёт); больше — закрашивается меньше, остатки рима "
                              "могут попасть в капли.",
    "RIM_CLOSE_RADIUS": "Радиус склейки соседних тёмных пятен рима, px. Больше — рим и брызги сливаются "
                        "в один объект и закрашиваются целиком; меньше — между ними остаются щели, "
                        "куски рима станут «каплями».",
    "RIM_DILATE_RADIUS": "На сколько расширить закрашенное вокруг рима, px. Больше — стирается больше "
                         "вокруг бэга, в том числе настоящие капли рядом; меньше — остаётся светлая "
                         "фиолетовая кайма, она даст ложные капли.",
    "DROPLET_GRAY_RANGE": "Растяжение яркости перед поиском границ. Уже диапазон — контрастнее: находятся "
                          "и слабые капли, но больше шума; шире — только контрастные капли.",
    "CANNY_THRESHOLD": "Порог границ (доля от максимума градиента в кадре). Меньше — больше контуров "
                       "и «капель», в том числе шумовых; больше — только яркие границы, слабые капли пропадают.",
    "DROPLET_OPEN_RADIUS": "Отделяет сплошные капли от тонких линий, px. Больше — в капли идут только "
                           "сплошные пятна; меньше — в капли попадают обрывки контуров.",
    "DROPLET_MIN_PIXELS": "Контуры меньше этого числа пикселей — шум. Больше — мелкие капли пропадают; "
                          "меньше — появляется мелкий мусор.",
    "DROPLET_CLOSE_RADIUS": "Замыкает разрывы контуров, px. Больше — разорванная капля станет сплошной, "
                            "но соседние капли могут слипнуться; меньше — разомкнутый контур пропадёт.",
    "PIV_WINDOW": "Размер окна корреляции, px. Больше — меньше ложных векторов, но сглаживает мелкие "
                  "течения; меньше — детальнее и шумнее. Должно быть заметно больше 2× PIV_MAX_DISPLACEMENT.",
    "PIV_STEP": "Шаг сетки PIV, px. Меньше — больше векторов и дольше счёт; больше — быстрее и грубее.",
    "PIV_MAX_DISPLACEMENT": "Наибольшее смещение между кадрами, которое ищет PIV, px. Меньше реального — "
                            "быстрые капли дают неверные векторы; больше — медленнее и больше ложных пиков.",
    "PIV_MIN_PEAK_RATIO": "Во сколько раз лучший пик корреляции должен быть выше второго. Больше — строже "
                          "(векторов меньше, но надёжнее); меньше — больше векторов и шума.",
    "PIV_FILTER_MIN_PEAK_RATIO": "То же при фильтрации готовых векторов: больше — сильнее чистка, меньше — "
                                 "остаётся больше векторов.",
    "PIV_FILTER_MIN_DISPLACEMENT": "Смещение меньше этого, px/кадр, считается ошибкой и заменяется медианой. "
                                   "Больше — сильнее давятся почти неподвижные области; меньше — они остаются.",
    "PIV_FILTER_MEDIAN_SIZE": "Окно медианного сглаживания векторов, узлов. Больше — сильнее убирает "
                              "одиночные выбросы, но сглаживает течение; меньше — детальнее и шумнее.",
    "PTV_SEARCH_RADIUS": "В каком радиусе от предсказания искать каплю, px. Больше — не потеряет быструю "
                         "каплю, но может связать чужую; меньше — наоборот.",
    "PTV_MAX_RADIUS_RATIO": "Во сколько раз может отличаться радиус капли в траектории. Больше — терпимее "
                            "(длиннее траектории, но риск связать разные капли); меньше — строже "
                            "(траектории рвутся).",
    "PTV_MAX_FRAME_GAP": "Какой пропуск кадров допускается внутри траектории. Больше — сшивает через "
                         "пропуски (длиннее траектории, больше ошибок); меньше — только подряд идущие кадры.",
    "PTV_MAX_DEVIATION": "Допустимое отклонение от полинома (в долях шага траектории). Больше — принимается "
                         "больше точек (длиннее траектории, но больше ошибок); меньше — только гладкое движение.",
    "PTV_MIN_STRAIGHTNESS": "Насколько три последние точки должны быть на прямой: 1 — точно на прямой. "
                            "Меньше — разрешает изгибы (и ошибки); больше — только прямые траектории.",
    "PTV_PIV_WEIGHT": "Вес PIV в предсказании положения. 0 — только по самой траектории; больше — сильнее "
                      "доверяет полю скоростей (помогает в плотном потоке, но при плохом PIV уводит не туда).",
    "PTV_POLY_POINTS": "По скольким последним точкам строится полином. Больше — устойчивее к шуму, но хуже "
                       "повороты; меньше — отзывчивее и шумнее.",
    "TRACKS_PLOT_MIN_POINTS": "Только картинка tracks.png: траектории длиннее этого — синие, короче — "
                              "оранжевые. На расчёт не влияет.",
    "AREA_PLOT_ARROW_SCALE": "Только картинки st4: длина стрелок скорости. На расчёт не влияет.",
    "AREA_PLOT_Y_LIMITS_MM": "Только картинки st4: пределы по y, мм. На расчёт не влияет.",
    "PIV_PLOT_ARROW_SCALE": "Только вид 4–5 в окне: длина стрелок PIV. На расчёт не влияет.",
    "PIV_PLOT_SPEED_MAX": "Только вид 5: верх цветовой шкалы скорости, м/с. На расчёт не влияет.",
}


def slider_range(value: float) -> tuple[float, float]:
    """Пределы ползунка числа: от 0 (у отрицательных — от удвоенного значения) до удвоенного значения;
    у целых — не уже 10 в каждую сторону, у нуля — 0…1 (дробные) или 0…10 (целые)."""
    span = float(abs(value)) if value else 1.0
    if isinstance(value, int):
        span = max(span, 10.0)
    low = value - span if value < 0 else max(0.0, value - span)
    return low, value + span


def number_text(value: float, step: float) -> str:
    """Дробное число с точностью шага ползунка, без лишних нулей и без записи вида 1e-07."""
    decimals = max(1, math.ceil(-math.log10(step))) if step > 0 else 6
    text = f"{value:.{decimals}f}".rstrip("0")
    text = text + "0" if text.endswith(".") else text
    return "0.0" if text in ("-0.0", "") else text


def section_rank(section: str) -> int:
    """Место раздела в панели по SECTION_ORDER (общие, st1, st2, st4, графики); неизвестные — в конце."""
    text = section.strip().lower()
    return next((i for i, prefix in enumerate(SECTION_ORDER) if text.startswith(prefix)), len(SECTION_ORDER))


def _numbers(value: Any) -> list[float] | None:
    """Числа, которым нужны ползунки: само число или кортеж из 1–MAX_SLIDERS чисел; иначе None."""
    def is_number(v: Any) -> bool:
        return isinstance(v, (int, float)) and not isinstance(v, bool)

    if is_number(value):
        return [value]
    if isinstance(value, tuple) and 0 < len(value) <= MAX_SLIDERS and all(is_number(v) for v in value):
        return list(value)
    return None


class ParamField(QWidget):
    """Одно значение файла настроек: подпись, поле ввода, ползунки для чисел, практическое пояснение
    (HINTS: что меняется на кадре и в какую сторону крутить).
    kind — "settings" или "thresholds"; setting — значение из файла (config_edit.Setting);
    hint — подпись с пояснением (None — для значения пояснения нет).
    У SURFACE_ROW рядом с полем — кнопка «по кадру»: значение задаётся щелчком по кадру
    (pick — сигнал с именем значения)."""

    edited = Signal(str, str, str)  # kind, имя, новый текст значения (как в файле)
    finished = Signal()  # поле доделали: ушли из него или отпустили ползунок (серия правок закончена)
    pick = Signal(str)  # нажали «по кадру» (имя значения)

    def __init__(self, kind: str, setting: Setting) -> None:
        super().__init__()
        self.kind, self.setting = kind, setting
        self.is_text = isinstance(setting.value, str)
        self.is_tuple = isinstance(setting.value, tuple)
        label = QLabel(f"<b>{setting.name}</b>" + (f" — {html.escape(setting.comment)}" if setting.comment else ""))
        label.setWordWrap(True)
        label.setToolTip(setting.note or setting.comment)
        self.edit = QLineEdit()
        self.edit.setToolTip(setting.note or setting.comment)
        self.edit.textEdited.connect(self._typed)
        self.edit.editingFinished.connect(self.finished.emit)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(2)
        layout.addWidget(label)
        edit_row = QHBoxLayout()
        edit_row.addWidget(self.edit, 1)
        self.pick_button: QToolButton | None = None
        if kind == "settings" and setting.name == "SURFACE_ROW":  # значение можно задать щелчком по кадру
            self.pick_button = QToolButton()
            self.pick_button.setText("по кадру")
            self.pick_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            self.pick_button.setToolTip("Щёлкнуть по кадру: строка y (с 1) станет SURFACE_ROW — в settings.py "
                                        "или в колонку таблицы, имя которой записано в SURFACE_ROW")
            self.pick_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.pick_button.clicked.connect(lambda checked=False: self.pick.emit(self.setting.name))
            edit_row.addWidget(self.pick_button)
        layout.addLayout(edit_row)
        self.sliders: list[QSlider] = []
        self.ranges: list[tuple[float, float]] = []
        self.integers: list[bool] = []
        self.sliding = False  # правка идёт от ползунка: его пределы не пересчитывать, пока его тянут
        numbers = _numbers(setting.value)
        for i, number in enumerate(numbers or []):
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            slider.valueChanged.connect(lambda position, i=i: self._slid(i, position))
            slider.sliderReleased.connect(self.finished.emit)
            row = QHBoxLayout()
            if len(numbers) > 1:
                row.addWidget(QLabel(f"{i + 1}:"))
            row.addWidget(slider, 1)
            layout.addLayout(row)
            self.sliders.append(slider)
            self.ranges.append((0.0, 1.0))
            self.integers.append(isinstance(number, int))
        self.hint: QLabel | None = None
        text = HINTS.get(setting.name)
        if text:
            self.hint = QLabel(text)
            self.hint.setWordWrap(True)
            self.hint.setStyleSheet("color: #555555;")
            layout.addWidget(self.hint)

    def matches(self, words: str) -> bool:
        """Подходит ли поле под поиск (имя, комментарий, пояснение, раздел)."""
        s = self.setting
        return words.lower() in f"{s.name} {s.comment} {s.note} {s.section} {HINTS.get(s.name, '')}".lower()

    def show_text(self, text: str, changed: bool, keep_text: bool = False) -> None:
        """Показать значение (текст литерала из файла или правки), отметку правки, положение ползунков.
        keep_text — не трогать набираемый текст; ползунки при этом встают серединой на новое значение."""
        if not keep_text:
            shown = literal(text) if self.is_text else text
            if self.edit.text() != shown:
                self.edit.setText(shown)
        self.mark(changed)
        numbers = _numbers(literal(text))
        if numbers is not None and len(numbers) == len(self.sliders) and not self.sliding:
            self._place_sliders(numbers, recenter=True)

    def _place_sliders(self, numbers: list[float], recenter: bool) -> None:
        """Ползунки — на числа numbers (recenter — сначала пересчитать пределы вокруг них)."""
        for i, (slider, number) in enumerate(zip(self.sliders, numbers)):
            if recenter:
                self.ranges[i] = slider_range(number)
            low, high = self.ranges[i]
            steps = int(round(high - low)) if self.integers[i] else FLOAT_STEPS
            slider.blockSignals(True)
            slider.setRange(0, max(steps, 1))
            slider.setValue(int(round((number - low) / (high - low) * steps)) if high > low else 0)
            slider.blockSignals(False)

    def mark(self, changed: bool, error: str = "") -> None:
        """Подсветка: жёлтым — несохранённая правка, красным — ошибка ввода (текст ошибки — в подсказке)."""
        self.edit.setStyleSheet(INVALID_STYLE if error else CHANGED_STYLE if changed else "")
        tip = self.setting.note or self.setting.comment
        self.edit.setToolTip(f"{error}\n\n{tip}" if error else tip)

    def _typed(self, text: str) -> None:
        """Набран текст: строки — без кавычек (кавычки добавляются сами), остальное — как в файле."""
        self.edited.emit(self.kind, self.setting.name, value_text(text) if self.is_text else text)

    def _slid(self, i: int, position: int) -> None:
        """Ползунок i сдвинут: новое число в поле (у кортежа — меняется только i-е число)."""
        try:
            numbers = _numbers(literal(self.edit.text()))
        except ValueError:
            numbers = None
        numbers = numbers if numbers is not None and len(numbers) == len(self.sliders) else _numbers(self.setting.value)
        low, high = self.ranges[i]
        if self.integers[i]:
            numbers[i] = int(round(low)) + position
            texts = [str(n) if isinstance(n, int) else repr(n) for n in numbers]
        else:
            numbers[i] = low + (high - low) * position / FLOAT_STEPS
            step = (high - low) / FLOAT_STEPS
            texts = [number_text(n, step) if k == i else (str(n) if isinstance(n, int) else repr(n))
                     for k, n in enumerate(numbers)]
        if self.is_tuple:
            text = "(" + ", ".join(texts) + ("," if len(texts) == 1 else "") + ")"
        else:
            text = texts[0]
        self.edit.setText(text)
        self.sliding = True
        try:
            self.edited.emit(self.kind, self.setting.name, text)
        finally:
            self.sliding = False


class ParamsPanel(QWidget):
    """Панель «Настройки»: значения settings.py и thresholds.py эксперимента с несохранёнными правками.

    load — открыть файлы эксперимента (модули settings и thresholds); working — значения с правками
    (для расчёта кадров); save — записать правки в файлы; undo / redo — отменить / повторить правку
    значения (Ctrl+Z / Ctrl+Y приложения; правки одного значения подряд — один шаг). Если настройки
    не из файлов (например, в тестах), панель только сообщает об этом, а working возвращает их как есть.
    У SURFACE_ROW — кнопка «по кадру» (pick_requested): RecordsPage ждёт щелчок по кадру и пишет значение
    в settings.py или в колонку таблицы, имя которой записано в SURFACE_ROW.
    """

    edited = Signal()  # значения с правками изменились — пересчитать кадр
    changed = Signal()  # изменились несохранённые правки
    message = Signal(str)  # сообщение для строки состояния
    step_made = Signal(str)  # новая правка для общей истории отмены (имя значения)
    history_reset = Signal()  # история отмены забыта (сохранение, отказ от правок, другие файлы)
    pick_requested = Signal(str)  # нажали «по кадру» у значения (SURFACE_ROW): RecordsPage ждёт щелчок по кадру

    def __init__(self) -> None:
        super().__init__()
        self.modules: dict[str, Any] = {}
        self.files: dict[str, SettingsFile] = {}
        self.fields: dict[tuple[str, str], ParamField] = {}
        self.headers: list[tuple[QLabel, list[ParamField]]] = []  # заголовки разделов и их поля (для поиска)
        self.history: list[tuple[str, str, str, str]] = []  # шаги отмены: kind, имя, текст до, после
        self.redo_history: list[tuple[str, str, str, str]] = []
        self._merge: tuple[str, str] | None = None  # правки этого значения подряд — один шаг
        self.where = QLabel()
        self.where.setWordWrap(True)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Найти: имя, слово из комментария или пояснения")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        self.save_button = QPushButton("Сохранить настройки")
        self.save_button.clicked.connect(lambda: self.save())
        self.revert_button = QPushButton("Вернуть как в файлах")
        self.revert_button.clicked.connect(self.discard)
        for b in (self.save_button, self.revert_button):
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.changes_label = QLabel()
        buttons = QHBoxLayout()
        buttons.addWidget(self.save_button)
        buttons.addWidget(self.revert_button)
        self.form = QVBoxLayout()
        content = QWidget()
        column = QVBoxLayout(content)
        column.addLayout(self.form)
        column.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(content)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self.where)
        layout.addWidget(self.search)
        layout.addLayout(buttons)
        layout.addWidget(self.changes_label)
        layout.addWidget(scroll, 1)

    @property
    def has_changes(self) -> bool:
        """Есть ли несохранённые правки настроек."""
        return any(f.changed() for f in self.files.values())

    def load(self, settings: ModuleType, thresholds: ModuleType) -> None:
        """Открыть файлы эксперимента. Если это те же файлы, несохранённые правки и история отмены
        остаются (если ещё подходят); другие файлы — история забывается."""
        old = {kind: (f.path, dict(f.edits)) for kind, f in self.files.items()}
        self.modules = {"settings": settings, "thresholds": thresholds}
        self.files = {}
        for kind, module in self.modules.items():
            path = getattr(module, "__file__", None)
            if not path:
                continue
            self.files[kind] = SettingsFile(Path(path))
            if kind in old and old[kind][0] == Path(path):
                for name, text in old[kind][1].items():
                    if name in self.files[kind].settings:
                        try:
                            self.files[kind].set_text(name, text, allow_text=kind == "settings")
                        except ValueError:
                            pass
        same_files = ({kind: f.path for kind, f in self.files.items()}
                      == {kind: path for kind, (path, _) in old.items()})
        if not same_files:
            self._reset_history()
        self._build()

    def _build(self) -> None:
        """Поля всех значений файлов по разделам: общие настройки, st1, st2, st4, графики (SECTION_ORDER)."""
        while self.form.count():
            item = self.form.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self.fields, self.headers = {}, []
        if not self.files:
            self.where.setText("Настройки эксперимента не из файлов — здесь их не поправить.")
        else:
            self.where.setText("Файлы: " + ", ".join(str(f.path) for f in self.files.values()))
        groups: list[tuple[str, str, list[Setting]]] = []  # (вид файла, раздел, значения)
        for kind, f in self.files.items():
            section = None
            for setting in f.settings.values():
                if setting.section != section:
                    section = setting.section
                    groups.append((kind, section, []))
                groups[-1][2].append(setting)
        groups.sort(key=lambda group: section_rank(group[1]))  # устойчиво: равные — как в файлах
        for kind, section, settings in groups:
            header = QLabel(section or FILE_TITLES[kind])
            header.setWordWrap(True)
            header.setStyleSheet("color: #1f5fd0; padding-top: 6px;")
            self.form.addWidget(header)
            section_fields: list[ParamField] = []
            self.headers.append((header, section_fields))
            for setting in settings:
                field = ParamField(kind, setting)
                field.edited.connect(self._edited)
                field.finished.connect(self._break_merge)
                field.pick.connect(self.pick_requested.emit)
                field.show_text(self.files[kind].text(setting.name), self.files[kind].changed(setting.name))
                self.form.addWidget(field)
                self.fields[(kind, setting.name)] = field
                section_fields.append(field)
        self._filter(self.search.text())
        self._update_counts()

    def _filter(self, words: str) -> None:
        """Показать только поля, подходящие под поиск (и заголовки их разделов)."""
        words = words.strip()
        for field in self.fields.values():
            field.setVisible(field.matches(words))
        for header, fields in self.headers:
            header.setVisible(any(f.matches(words) for f in fields))

    def _update_counts(self) -> None:
        """Подпись «несохранённых правок» и доступность кнопок."""
        n = sum(len(f.edits) for f in self.files.values())
        self.changes_label.setText(f"Несохранённых правок: {n}" if n else "Все значения — как в файлах")
        self.save_button.setEnabled(n > 0)
        self.revert_button.setEnabled(n > 0)

    def _edited(self, kind: str, name: str, text: str) -> None:
        """Правка значения в поле или ползунком: проверить, запомнить шаг отмены, пересчитать кадр."""
        field = self.fields[(kind, name)]
        before = self.files[kind].text(name)
        try:
            self.files[kind].set_text(name, text, allow_text=kind == "settings")
        except ValueError as error:
            field.mark(False, str(error))
            return
        after = self.files[kind].text(name)
        field.show_text(after, self.files[kind].changed(name), keep_text=True)
        self._update_counts()
        if after != before:
            self._record(kind, name, before, after)
        self.edited.emit()
        self.changed.emit()

    def _record(self, kind: str, name: str, before: str, after: str) -> None:
        """Запомнить шаг отмены; правки одного значения подряд (набор, ползунок) — один шаг."""
        if self._merge == (kind, name) and self.history:
            self.history[-1] = (kind, name, self.history[-1][2], after)
        else:
            self.history.append((kind, name, before, after))
            self._merge = (kind, name)
            self.step_made.emit(name)
        self.redo_history.clear()

    def _break_merge(self) -> None:
        """Закончить серию правок одного значения: следующая правка — новый шаг отмены."""
        self._merge = None

    def undo(self) -> bool:
        """Отменить последнюю правку значения (Ctrl+Z приложения); False — отменять нечего."""
        self._break_merge()
        if not self.history:
            return False
        kind, name, before, _ = self.history[-1]
        if not self._apply(kind, name, before):
            self.history.pop()
            return False
        self.redo_history.append(self.history.pop())
        return True

    def redo(self) -> bool:
        """Повторить отменённую правку значения; False — повторять нечего."""
        self._break_merge()
        if not self.redo_history:
            return False
        kind, name, _, after = self.redo_history[-1]
        if not self._apply(kind, name, after):
            self.redo_history.pop()
            return False
        self.history.append(self.redo_history.pop())
        return True

    def _apply(self, kind: str, name: str, text: str) -> bool:
        """Поставить значение name текстом text (как в файле), не записывая шаг в историю."""
        f = self.files[kind]
        try:
            f.set_text(name, text, allow_text=kind == "settings")
        except (KeyError, ValueError):
            return False
        field = self.fields.get((kind, name))
        if field is not None:
            field.show_text(f.text(name), f.changed(name))
        self._update_counts()
        self.edited.emit()
        self.changed.emit()
        return True

    def _reset_history(self) -> None:
        """Забыть историю отмены (правки сохранены, забыты или открыты другие файлы)."""
        if not self.history and not self.redo_history:
            self._merge = None
            return
        self.history, self.redo_history, self._merge = [], [], None
        self.history_reset.emit()

    def set_text(self, kind: str, name: str, text: str) -> None:
        """Правка значения name (текст — как в файле), как если бы её набрали в поле."""
        field = self.fields[(kind, name)]
        try:
            field.edit.setText(literal(text) if field.is_text else text)
        except ValueError:
            field.edit.setText(text)
        self._edited(kind, name, text)

    def working(self) -> tuple[Any, Any]:
        """Настройки и пороги с несохранёнными правками — объекты с теми же именами, что модули."""
        result = []
        for kind in ("settings", "thresholds"):
            module = self.modules[kind]
            if kind not in self.files:
                result.append(module)
                continue
            values = {name: getattr(module, name) for name in dir(module) if name.isupper()}
            values.update(self.files[kind].values())
            result.append(SimpleNamespace(**values))
        return result[0], result[1]

    def discard(self) -> None:
        """Забыть правки («Вернуть как в файлах») и историю отмены."""
        for f in self.files.values():
            f.discard()
        self._reset_history()
        self._build()
        self.edited.emit()
        self.changed.emit()

    def save(self) -> bool:
        """Записать правки в файлы (только изменённые значения). True — записано или нечего записывать."""
        saved = []
        for kind, f in self.files.items():
            try:
                names = f.save()
            except (OSError, ValueError) as error:
                QMessageBox.warning(self, "Настройки не сохранены", f"{f.path.name}: {error}")
                return False
            for name in names:  # модуль в памяти — как файл (без перезапуска)
                setattr(self.modules[kind], name, f.value(name))
            saved += names
        self._reset_history()
        self._build()
        self.changed.emit()
        self.message.emit(f"Настройки сохранены: {', '.join(saved)}" if saved else "Несохранённых настроек нет")
        return True
