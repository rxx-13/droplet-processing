# Карта кода

Путеводитель по коду для человека и для нейросети: что где лежит, что что вызывает, какие данные
идут между шагами, где используется каждая настройка. Физика и формулы — в [METHOD.md](METHOD.md),
как запускать — в [README.md](README.md).

Подробности о каждой функции — в её описании в коде (docstring): что делает, что принимает (с единицами
и формой массивов), что возвращает, аналог в MATLAB. Здесь — связи между функциями.
Тест [tests/test_docs.py](tests/test_docs.py) следит, чтобы в таблицах этого файла были все функции
`lib/` и все настройки, и чтобы у каждой функции было описание.

## 1. Как читать проект

**Путь выполнения.** Пользователь запускает скрипт стадии из [main/](main/) → общий цикл `run_stage`
([lib/common/records.py](lib/common/records.py)) читает таблицу записей и для каждой записи вызывает
`process_record` этого скрипта → тот вызывает функции из [lib/](lib/). Настройки приходят двумя
модулями: `settings` и `thresholds` текущего эксперимента (выбирается в
[experiments/current.py](experiments/current.py)); их значения — атрибуты ЗАГЛАВНЫМИ буквами.

**Соглашения.**
- В файлах результатов номера кадров и капель и координаты — с 1, как в MATLAB. В массивах numpy
  индексы — с 0 (кадр k лежит в списке под номером k − 1).
- Изображение — массив (строки, столбцы) = (y, x); ось y направлена вниз. Яркость кадра — уровни файла
  (0…255 для 8-битных картинок), после растяжения — 0…1.
- Капли кадра: центры — массив (N, 2) с колонками x, y (px, с 1), радиусы — (N,), px.
- PIV: массивы по узлам сетки формы (nx, ny) — ось 0 = x (так хранит MATLAB), смещения — px/кадр.
- Траектория — матрица k×16, строка 1 — самый поздний кадр (колонки — в разделе 5).
- `ВОПРОС К СПЕЦИАЛИСТУ (Qn)` — места, перенесённые из MATLAB как есть, хотя выглядят как ошибки
  (список с объяснениями и замерами — [QUESTIONS.md](QUESTIONS.md)):

| Метка | Где в коде |
|---|---|
| Q1 | `filter_piv` — приоритет `&` и `\|` в фильтре PIV |
| Q2 | `Piv._window_displacement` — ветка «один пик корреляции» (результат тот же) |
| Q3 | `Tracker.track` — не используются кадры N−2 и N |
| Q4 | `Tracker._extend` — спор двух траекторий за каплю |
| Q5 | `Tracker._extend` — погрешность предсказания `abs(dx + i*dy)` |
| Q6 | `Tracker._extend` — деление 0/0 в двух проверках |
| Q7 | `DropletFinder.find` — «костыль» в углу кадра перед canny |
| Q10 | `tracks_in_area` — отбор точек траектории в области |
| Q11 | `centroids_and_diameters` (через `DropletFinder.find`) — радиус капли завышен на 0.2–0.8 px |

## 2. Стадии: что что вызывает и какие данные идут

### st1 — [main/st1_droplets_separation.py](main/st1_droplets_separation.py)

```
run_stage → read_records → [Record]
process_record(record, settings, thresholds)
├─ Frames(record.images_dir(IMAGES_DIR))              кадры записи
├─ Frames.gray(BACKGROUND_FRAME)                      фон
├─ RimRemover.from_thresholds, DropletFinder.from_thresholds, Piv.from_thresholds
├─ для каждого кадра start_frame … fin_frame:
│   ├─ RimRemover.remove(кадр, фон, record.rim)       → clean
│   │     remove_with_mask: stretch → ERASE_REGIONS → порог → close_ → вода → fill_holes
│   │                       → areas → крупные объекты → dilate → закрасить
│   ├─ Piv.compute(1 − clean предыдущего, 1 − clean)  → PivResult (со второго кадра)
│   │     _window_displacement → regional_max
│   └─ DropletFinder.find(clean)                      → центры, радиусы
│         fill_holes_gray → stretch → canny → fill_holes → open_ → remove_small
│         → close_ → fill_holes → centroids_and_diameters
├─ filter_piv_by_thresholds → filter_piv → median_ignoring_nan
├─ velocity_at_points(центры, PIV после фильтра)      → xd, yd в центрах капель
│     Interpolator → Delaunay (+ cKDTree для точек снаружи)
└─ save_mat × 3 (списки → ячейки MATLAB через to_cell)
```

| Шаг | Вход | Выход |
|---|---|---|
| `Frames.gray` | номер файла | кадр H×W, float64, уровни файла |
| `RimRemover.remove` | кадр, фон, `rim` (px²) | `clean` H×W, 0…1 (1 — фон и закрашенное) |
| `Piv.compute` | два кадра H×W (1 − clean) | `PivResult`: смещения (nx, ny), px/кадр |
| `DropletFinder.find` | `clean` | центры (N, 2) px с 1, радиусы (N,) px |
| `velocity_at_points` | центры, `PivResult` | xd, yd (N,) px/кадр |
| итог | — | `PIV_output_arr.mat`, `PIV_output_filtered_arr.mat`, `particles.mat` |

### st2 — [main/st2_ptv.py](main/st2_ptv.py)

```
process_record
├─ load_particles(record.st1_dir(OUTPUT_DIR))  → списки по кадрам: центры, радиусы, xd, yd
├─ Tracker(…PTV_…).track(…)                    → траектории (матрицы k×16)
│     _extend → PolyFit, PolyFit.value_and_delta, _dist2
├─ plot_tracks → draw_tracks                   → tracks.png
└─ save_tracks → to_cell, save_mat             → ptv_unfiltered.mat
```

### st4 — [main/st4_droplets_in_area.py](main/st4_droplets_in_area.py)

```
process_record
├─ load_cell(ptv_unfiltered.mat, "p_arr")      → траектории
├─ для каждой области (AREA_HEIGHTS × AREA_LOW_EDGES):
│   ├─ Area → tracks_in_area (segments_intersect) → средние по траекториям, px и px/кадр
│   ├─ px_to_mm, px_per_frame_to_m_per_s          → мм, м/с; радиус × SCALE × 1000 → мкм
│   ├─ area_file_name                             → имя файла области
│   └─ plot_area → draw_area; save_mat; save_dat
└─ save_dat(_all_stat.dat)
```

### Окно просмотра — [tools/st1_viewer.py](tools/st1_viewer.py) и [lib/st1/preview.py](lib/st1/preview.py)

```
Viewer (окно, клавиши, таймер, слайдшоу)
├─ read_records                                 записи текущего эксперимента
├─ RecordPreview (preview.py)                   данные одной записи
│   ├─ FrameJob на каждый кадр                  только простые данные — передаются в другие процессы
│   ├─ submit_all(ProcessPoolExecutor)          фоновый расчёт всех кадров
│   │     compute_frame(FrameJob) → FrameResult  = st1 для одного кадра:
│   │         RimRemover.remove_with_mask, DropletFinder.find, Piv.compute,
│   │         filter_piv_by_thresholds, velocity_at_points
│   ├─ collect / ready / result                 забрать готовое / проверить / дождаться
│   └─ _load_matlab → load_particles            капли MATLAB для сравнения
└─ ViewDrawer.show(preview, k, вид)             рисует вид на ось matplotlib
```
«Что показывать» (`preview.py`) отделено от «как показывать» (`st1_viewer.py`): другой способ просмотра
(серия PNG, видео, картинки во время st1) — новая короткая оболочка над `RecordPreview` и `ViewDrawer`.

## 3. Функции по файлам

Колонки: что делает; кто вызывает; что использует; аналог в MATLAB (если есть).

### [lib/common/files.py](lib/common/files.py) — кадры, .mat, .dat

| Функция | Что делает | Кто вызывает | Использует | MATLAB |
|---|---|---|---|---|
| `last_number_in_name` | последнее число в имени файла | `Frames` | — | ExtractNumber |
| `Frames` | кадры записи по порядку номеров, размер кадра | main/st1, `RecordPreview`, `_frames` | `last_number_in_name` | imLoader_anyext, FilesInFolder |
| `Frames.gray` | кадр № (с 1) как массив float64 | main/st1, `RecordPreview.frame`, `compute_frame`, `_background` | — | getImageDoubleGray |
| `to_cell` | список → ячейка MATLAB N×1 | main/st1, `save_tracks` | — | cell |
| `load_cell` | ячейка из .mat → список | `load_particles`, main/st4, tests | — | load |
| `save_mat` | запись .mat | main/st1, main/st4, `save_tracks` | — | save |
| `load_particles` | капли из particles.mat по кадрам | main/st2, `RecordPreview._load_matlab`, tests | `load_cell` | — |
| `_dat_number` | число в формате %16.7e | `save_dat` | — | — |
| `save_dat` | таблица в .dat как save -ascii | main/st4 | `_dat_number` | save -ascii |
| `load_dat` | .dat → таблица | tests | — | load |

### [lib/common/records.py](lib/common/records.py) — таблица записей и цикл стадии

| Функция | Что делает | Кто вызывает | Использует | MATLAB |
|---|---|---|---|---|
| `Record` | одна строка таблицы | `read_records` | — | строка rec_list |
| `Record.has_frames` | задан ли диапазон кадров | `run_stage`, `Viewer`, tests | — | — |
| `Record.images_dir` | папка кадров записи | main/st1, `RecordPreview` | — | fullfile(images_folder_base, …) |
| `Record.st1_dir` | папка результата st1 | main/st1, main/st2, `RecordPreview._load_matlab`, tests | — | outputFolder st1 |
| `Record.st2_dir` | папка результата st2 | main/st2, main/st4, tests | — | outputFolder st2 |
| `Record.st4_dir` | папка результата st4 | main/st4, tests | — | outputFolder st4 |
| `_text` | ячейка → текст | `read_records` | — | — |
| `_number` | ячейка → число или None | `read_records`, `_integer` | — | — |
| `_integer` | ячейка → целое или None | `read_records` | `_number` | — |
| `read_records` | записи из xlsx с выбором по условиям и именам | `run_stage`, `Viewer`, tests | `Record`, `_text`, `_number`, `_integer` | My_importfile_xls_with_headers_to_table_v1 |
| `run_stage` | цикл стадии по записям, лог, код выхода | main/st1, main/st2, main/st4 | `read_records`, `process_record` стадии | цикл по rr_i |

### [lib/st1/droplets.py](lib/st1/droplets.py) — удаление рима и поиск капель

| Функция | Что делает | Кто вызывает | Использует | MATLAB |
|---|---|---|---|---|
| `stretch` | растяжение яркости [low, high] → [0, 1] | `RimRemover.remove_with_mask`, `DropletFinder.find` | — | mat2gray |
| `RimRemover` | удаление рима, брызг, воды, ручных областей | main/st1, `compute_frame` | `disk` | блок «удаление крупных брызг» |
| `RimRemover.from_thresholds` | создать из thresholds и settings | main/st1, `compute_frame` | — | — |
| `RimRemover.remove` | кадр без рима | main/st1, `compute_frame` | `RimRemover.remove_with_mask` | im_b{7} |
| `RimRemover.remove_with_mask` | кадр без рима и маска закрашенного | `RimRemover.remove`, `compute_frame` | `stretch`, `close_`, `fill_holes`, `areas`, `dilate` | im_b{1}…im_b{7} |
| `DropletFinder` | поиск капель | main/st1, `compute_frame` | `disk` | блок «Выделение капель» |
| `DropletFinder.from_thresholds` | создать из thresholds | main/st1, `compute_frame` | — | — |
| `DropletFinder.find` | центры и радиусы капель | main/st1, `compute_frame` | `fill_holes_gray`, `stretch`, `canny`, `fill_holes`, `open_`, `remove_small`, `close_`, `centroids_and_diameters` | im_e{1}…im_e{12}, regionprops |

### [lib/st1/image_ops.py](lib/st1/image_ops.py) — операции над изображениями, как в MATLAB

| Функция | Что делает | Кто вызывает | Использует | MATLAB |
|---|---|---|---|---|
| `_minkowski_sum` | сумма формы и набора сдвигов | `disk` | — | imdilate 'full' |
| `disk` | структурный элемент «диск» (восьмиугольник при R ≥ 3) | `RimRemover`, `DropletFinder` | `_minkowski_sum` | strel('disk', R) |
| `dilate` | расширение | `RimRemover.remove_with_mask`, `close_`, `open_` | — | imdilate |
| `erode` | сужение | `close_`, `open_` | — | imerode |
| `close_` | закрытие (сначала расширить, потом сузить) | `RimRemover.remove_with_mask`, `DropletFinder.find` | `dilate`, `erode` | imclose |
| `open_` | открытие (сначала сузить, потом расширить) | `DropletFinder.find` | `dilate`, `erode` | imopen |
| `fill_holes` | заливка дыр в чёрно-белом изображении | `RimRemover.remove_with_mask`, `DropletFinder.find` | — | imfill(BW, 'holes') |
| `fill_holes_gray` | заливка дыр в полутоновом изображении | `DropletFinder.find` | — | imfill(I, 'holes') |
| `remove_small` | убрать объекты меньше N пикселей | `DropletFinder.find` | — | bwareaopen |
| `label` | нумерация объектов по столбцам | `areas`, `centroids_and_diameters`, `canny` | — | bwlabel |
| `centroids_and_diameters` | центры масс и эквивалентные диаметры | `DropletFinder.find` | `label` | regionprops Centroid, EquivDiameter |
| `areas` | метки и площади объектов | `RimRemover.remove_with_mask` | `label` | regionprops Area |
| `regional_max` | локальные максимумы | `Piv._window_displacement` | — | imregionalmax |
| `_smooth_gradient` | градиент сглаженного изображения | `canny` | — | smoothGradient в edge.m |
| `_thin_edges` | подавление немаксимумов | `canny` | — | cannyFindLocalMaxima |
| `canny` | карта границ | `DropletFinder.find` | `_smooth_gradient`, `_thin_edges`, `label` | edge(I, 'canny', T) |

### [lib/st1/piv.py](lib/st1/piv.py) — PIV

| Функция | Что делает | Кто вызывает | Использует | MATLAB |
|---|---|---|---|---|
| `PivGrid` | узлы сетки PIV | `Piv` | — | PIV_Grid |
| `PivGrid.regular` | регулярная сетка с шагом | `Piv` | — | PIV_Grid(0:step:W, 0:step:H) |
| `PivGrid.shape` | (nx, ny) | `Piv.compute` | — | — |
| `PivGrid.x_mat` | x узлов, (nx, ny) | `velocity_at_points`, `ViewDrawer.show`, `PivGrid.to_struct` | — | xMat |
| `PivGrid.y_mat` | y узлов, (nx, ny) | `velocity_at_points`, `ViewDrawer.show`, `PivGrid.to_struct` | — | yMat |
| `PivGrid.to_struct` | поля PIV_Grid для .mat | `Piv` | — | — |
| `PivResult` | результат PIV пары кадров | `Piv.compute`, `filter_piv` | — | PIV_output |
| `PivResult.to_struct` | структура PIV_output для .mat | main/st1 | — | — |
| `Piv` | PIV на сетке | main/st1, `compute_frame` | `PivGrid` | myf_DoCC8_multimax |
| `Piv.from_thresholds` | создать из thresholds | main/st1, `compute_frame` | — | PIV_params |
| `Piv.compute` | смещения во всех узлах | main/st1, `compute_frame` | `Piv._window_displacement` | myf_DoCC8_multimax |
| `Piv._window_displacement` | смещение по корреляции одного окна | `Piv.compute` | `regional_max` | тело цикла по окнам |
| `median_ignoring_nan` | медиана по окну без NaN | `filter_piv` | — | mediannan |
| `filter_piv` | отбраковка и медиана | `filter_piv_by_thresholds`, tests | `median_ignoring_nan` | Filter_PIV_output |
| `filter_piv_by_thresholds` | filter_piv с порогами из thresholds | main/st1, `compute_frame` | `filter_piv` | — |

### [lib/st1/velocity.py](lib/st1/velocity.py) — скорость PIV в центрах капель

| Функция | Что делает | Кто вызывает | Использует | MATLAB |
|---|---|---|---|---|
| `velocity_at_points` | смещения PIV в центрах капель | main/st1, `compute_frame` | `Interpolator` | scatteredInterpolant ×2 |
| `Interpolator` | линейная интерполяция, снаружи — ближайший узел | `velocity_at_points` | `Delaunay`, cKDTree | scatteredInterpolant |
| `_exact` | точное (рациональное) значение float | `Delaunay` | — | — |
| `_incircle_exact` | точная проверка «внутри окружности» | `Delaunay._insert` | — | — |
| `Delaunay` | триангуляция с вставкой точек по одной | `Interpolator` | `Delaunay._add`, `Delaunay._insert` | delaunayTriangulation |
| `Delaunay._add` | добавить треугольник | `Delaunay`, `Delaunay._insert` | — | — |
| `Delaunay._insert` | вставить точку | `Delaunay` | `Delaunay._add`, `_incircle_exact` | — |
| `Delaunay.find` | треугольник и барицентрические веса точек | `Interpolator` | — | pointLocation |

### [lib/st1/preview.py](lib/st1/preview.py) — что показывать в окне просмотра

| Функция | Что делает | Кто вызывает | Использует | MATLAB |
|---|---|---|---|---|
| `FrameJob` | задание на расчёт одного кадра | `RecordPreview` | — | — |
| `FrameResult` | результат расчёта кадра | `compute_frame` | — | — |
| `FrameResult.erased_mask` | маска закрашенного | `ViewDrawer.show` | — | — |
| `_frames` | кадры записи, запоминаются в процессе | `compute_frame`, `_background` | `Frames` | — |
| `_background` | кадр фона, запоминается | `compute_frame` | `_frames` | — |
| `compute_frame` | st1 для одного кадра | `RecordPreview.result`, `RecordPreview.submit_all` | `RimRemover`, `DropletFinder`, `Piv`, `filter_piv_by_thresholds`, `velocity_at_points` | st1 для кадра |
| `RecordPreview` | кадры записи, расчёт по требованию и в фоне | `Viewer`, tests | `Frames`, `FrameJob` | — |
| `RecordPreview._load_matlab` | капли MATLAB для сравнения | `RecordPreview` | `load_particles` | — |
| `RecordPreview.frame` | исходный кадр k | `ViewDrawer.show` | `Frames.gray` | — |
| `RecordPreview.matlab_droplets` | капли MATLAB на кадре k | `ViewDrawer.show` | — | — |
| `RecordPreview.submit_all` | отправить кадры в фоновый расчёт | `Viewer` | `compute_frame` | — |
| `RecordPreview.collect` | забрать готовые кадры | `Viewer`, `RecordPreview.ready` | — | — |
| `RecordPreview.ready` | посчитан ли кадр | `Viewer` | `RecordPreview.collect` | — |
| `RecordPreview.result` | посчитанный кадр (дождаться или посчитать) | `ViewDrawer.show`, tests | `compute_frame` | — |
| `RecordPreview.cancel` | отменить фоновый расчёт | `Viewer` | — | — |
| `_circles` | окружности капель для рисования | `ViewDrawer.show` | — | viscircles |
| `ViewDrawer` | рисование видов на ось | `Viewer` | — | — |
| `ViewDrawer.reset` | начать заново (другая запись) | `Viewer` | `ViewDrawer._clear_overlays`, `ViewDrawer._remove_colorbar` | — |
| `ViewDrawer._clear_overlays` | убрать наложения прошлого кадра | `ViewDrawer.show`, `ViewDrawer.reset` | — | — |
| `ViewDrawer._remove_colorbar` | убрать цветовую шкалу | `ViewDrawer.show`, `ViewDrawer.reset` | — | — |
| `ViewDrawer._base` | картинка-основа | `ViewDrawer.show` | — | imshow |
| `ViewDrawer._add` | запомнить наложение | `ViewDrawer.show` | — | — |
| `ViewDrawer.show` | нарисовать вид кадра | `Viewer` | `RecordPreview.frame`, `RecordPreview.result`, `_circles` | pcolor, quiver, viscircles |

### [lib/st2/tracking.py](lib/st2/tracking.py) — PTV-трекинг

| Функция | Что делает | Кто вызывает | Использует | MATLAB |
|---|---|---|---|---|
| `save_tracks` | траектории в ptv_unfiltered.mat | main/st2 | `to_cell`, `save_mat` | save p_arr |
| `draw_tracks` | траектории на оси | `plot_tracks` | — | plot |
| `plot_tracks` | tracks.png | main/st2 | `draw_tracks` | saveas |
| `PolyFit` | МНК-полином с оценкой погрешности | `Tracker._extend` | — | polyfit с S |
| `PolyFit.value_and_delta` | значение полинома и его погрешность | `Tracker._extend` | — | polyval с S |
| `_dist2` | квадраты расстояний | `Tracker._extend` | — | dist2 |
| `Tracker` | трекинг назад по времени | main/st2 | — | st2_PTV_mod2.m |
| `Tracker.track` | все траектории записи | main/st2 | `Tracker._extend` | цикл по кадрам i |
| `Tracker._extend` | продлить одну траекторию каплей кадра i | `Tracker.track` | `PolyFit`, `_dist2` | цикл по p_arr_i |

### [lib/st4/areas.py](lib/st4/areas.py) — статистика по областям

| Функция | Что делает | Кто вызывает | Использует | MATLAB |
|---|---|---|---|---|
| `Area` | прямоугольная область поиска, px | main/st4 | — | search_area_x, search_area_y |
| `px_to_mm` | положение px → мм от (cx, cy) | main/st4, `draw_area` | — | px2mm |
| `px_per_frame_to_m_per_s` | px/кадр → м/с | main/st4 | — | vpx2vmps |
| `segments_intersect` | пересечения отрезков | `tracks_in_area` | — | lineSegmentIntersect |
| `tracks_in_area` | средние по траекториям в области | main/st4 | `segments_intersect` | st4_…_v1_function2 |
| `area_file_name` | имя файла области | main/st4 | — | sprintf имени |
| `draw_area` | картинка области на двух осях | `plot_area` | `px_to_mm` | plot, quiver |
| `plot_area` | картинка области в файл | main/st4 | `draw_area` | saveas |

### [main/](main/) и [tools/](tools/)

- `process_record` в каждом скрипте `main/` — обработка одной записи стадии (деревья — в разделе 2);
  вызывается из `run_stage`, а в тестах — напрямую.
- [tools/st1_viewer.py](tools/st1_viewer.py): `Viewer` — окно; методы `press`, `release` (клавиши),
  `tick` (таймер: фоновые результаты и слайдшоу), `draw` и `_title` (рисование и заголовок),
  `_open`, `_switch`, `_reload` (открыть запись, сменить запись, перечитать настройки).
- [tools/make_docx.py](tools/make_docx.py): Word-версии документации в папку `docx/` (pandoc);
  `fix_links` переписывает ссылки для документа в подпапке, `convert` — один документ.

## 4. Настройки и пороги

Настройки эксперимента — `experiments/<эксперимент>/settings.py`, пороги — `thresholds.py` там же,
значения отдельных записей — таблица xlsx.

| Имя | Где задано | Кто использует | На что влияет |
|---|---|---|---|
| `IMAGES_DIR` | settings | main/st1, `RecordPreview` | где искать кадры |
| `TABLE_FILE` | settings | `run_stage`, `Viewer`, tests | таблица записей |
| `OUTPUT_DIR` | settings | main/st1, main/st2, main/st4 | куда писать результаты |
| `CONDITIONS` | settings | `run_stage`, `Viewer` | какие условия обрабатывать |
| `RECORDS` | settings | `run_stage`, `Viewer` | какие записи обрабатывать |
| `SCALE_MM_PER_PX` | settings | main/st4, `RecordPreview` | перевод px → мм, px/кадр → м/с |
| `FPS` | settings | main/st4, `RecordPreview` | перевод px/кадр → м/с |
| `SURFACE_ROW` | settings | `RimRemover` (main/st1, `compute_frame`) | откуда вниз закрашивается вода |
| `BACKGROUND_FRAME` | settings | main/st1, `compute_frame` | какой кадр — фон |
| `ERASE_REGIONS` | settings | `RimRemover` (main/st1, `compute_frame`) | вручную закрашиваемые области |
| `AREA_X` | settings | main/st4 | границы областей по x |
| `AREA_HEIGHTS` | settings | main/st4 | высоты областей |
| `AREA_LOW_EDGES` | settings | main/st4 | положения нижних краёв областей |
| `MATLAB_RESULTS_DIR` | settings | `RecordPreview`, tests/compare_with_matlab.py | где результаты MATLAB для сравнения |
| `RIM_GRAY_RANGE` | thresholds | `RimRemover.from_thresholds` | растяжение «кадр − фон» |
| `RIM_BINARIZE_THRESHOLD` | thresholds | `RimRemover.from_thresholds` | что считать тёмным объектом |
| `RIM_CLOSE_RADIUS` | thresholds | `RimRemover.from_thresholds` | насколько склеивать тёмное в объекты |
| `RIM_DILATE_RADIUS` | thresholds | `RimRemover.from_thresholds` | на сколько расширять закрашенное |
| `DROPLET_GRAY_RANGE` | thresholds | `DropletFinder.from_thresholds` | растяжение яркости перед canny |
| `CANNY_THRESHOLD` | thresholds | `DropletFinder.from_thresholds` | чувствительность к границам капель |
| `DROPLET_OPEN_RADIUS` | thresholds | `DropletFinder.from_thresholds` | отделение тонких контуров от сплошных капель |
| `DROPLET_MIN_PIXELS` | thresholds | `DropletFinder.from_thresholds` | самый мелкий контур, не считающийся шумом |
| `DROPLET_CLOSE_RADIUS` | thresholds | `DropletFinder.from_thresholds` | замыкание разорванных контуров |
| `PIV_WINDOW` | thresholds | `Piv.from_thresholds` | размер окна корреляции |
| `PIV_STEP` | thresholds | `Piv.from_thresholds` | шаг сетки PIV |
| `PIV_MAX_DISPLACEMENT` | thresholds | `Piv.from_thresholds` | наибольшее смещение за кадр |
| `PIV_MIN_PEAK_RATIO` | thresholds | `Piv.from_thresholds` | отбраковка неуверенных векторов при расчёте |
| `PIV_FILTER_MIN_PEAK_RATIO` | thresholds | `filter_piv_by_thresholds` | отбраковка при фильтрации |
| `PIV_FILTER_MIN_DISPLACEMENT` | thresholds | `filter_piv_by_thresholds`, `Piv.from_thresholds` | отбрасывание почти неподвижных векторов |
| `PIV_FILTER_MEDIAN_SIZE` | thresholds | `filter_piv_by_thresholds` | окно медианы |
| `PTV_SEARCH_RADIUS` | thresholds | main/st2 → `Tracker` | радиус поиска капли |
| `PTV_MAX_RADIUS_RATIO` | thresholds | main/st2 → `Tracker` | допустимое изменение радиуса |
| `PTV_MAX_FRAME_GAP` | thresholds | main/st2 → `Tracker` | допустимый пропуск кадров |
| `PTV_MAX_DEVIATION` | thresholds | main/st2 → `Tracker` | отклонение от полинома |
| `PTV_MIN_STRAIGHTNESS` | thresholds | main/st2 → `Tracker` | прямолинейность |
| `PTV_PIV_WEIGHT` | thresholds | main/st2 → `Tracker` | вес PIV в предсказании |
| `PTV_POLY_POINTS` | thresholds | main/st2 → `Tracker` | число точек для полинома |
| `TRACKS_PLOT_MIN_POINTS` | thresholds | main/st2 → `plot_tracks` | граница «длинная/короткая» на tracks.png |
| `AREA_PLOT_ARROW_SCALE` | thresholds | main/st4 → `plot_area` | длина стрелок на картинках st4 |
| `AREA_PLOT_Y_LIMITS_MM` | thresholds | main/st4 → `plot_area` | пределы по y на картинках st4 |
| `PIV_PLOT_ARROW_SCALE` | thresholds | `RecordPreview` → `ViewDrawer.show` | длина стрелок PIV в окне просмотра |
| `PIV_PLOT_SPEED_MAX` | thresholds | `RecordPreview` → `ViewDrawer.show` | верх цветовой шкалы скорости в окне |
| `rim` / `rim_new` | таблица xlsx | `RimRemover.remove` | порог площади рима и крупных брызг, px² |
| `start_frame`, `fin_frame` | таблица xlsx | main/st1, `RecordPreview` | диапазон кадров записи |
| `cx` / `c_x`, `cy` / `c_y` | таблица xlsx | main/st4 | начало координат в мм |

## 5. Словарь данных

**`Record`** (строка таблицы): `name`, `path` (путь записи относительно IMAGES_DIR), `cond_num`,
`start_frame`, `fin_frame`, `rim`, `cx`, `cy`.

**`particles.mat`** (st1, ячейки N×1 по кадрам, пустой кадр — `[]`):

| Переменная | Форма | Что |
|---|---|---|
| `centers_arr` | n×2 | x, y центров капель, px с 1 |
| `radii_arr` | n×1 | радиусы, px |
| `xd_q_arr`, `yd_q_arr` | n×1 | скорость PIV в центре капли, px/кадр (y вниз) |

**`PIV_output_arr.mat`, `PIV_output_filtered_arr.mat`** (st1, ячейки N×1, последняя пустая): структура
`PIV_output` = `PivResult.to_struct`: `status`, `xdispl`, `ydispl`, `CC_maxValue`, `CC_maxRaitio` (опечатка
MATLAB сохранена), `PIV_params` (с `piv_grid`), `imSize`, `mean_im1`, `time_of_iteration`.

**Траектория `p_arr`** (`ptv_unfiltered.mat`, st2) — матрица k×16, строка 1 — самый поздний кадр:

| Колонка | Что | Колонка | Что |
|---|---|---|---|
| 1 | номер кадра в диапазоне (с 1) | 9–10 | предсказание по PIV, px |
| 2 | номер капли на кадре (с 1) | 11–12 | предсказание полиномом, px |
| 3 | расстояние до предсказания, px | 13–14 | итоговое предсказание, px |
| 4–5 | x, y, px | 15 | погрешность полинома |
| 6 | радиус, px | 16 | радиус поиска, px |
| 7–8 | скорость PIV xd, yd, px/кадр | | |

**st4:** `<область>.dat` — по строке на траекторию: x мм, y мм, vx м/с, vy м/с, r мкм (нет траекторий —
`0`); `<область>.mat` — то же + в px (`p_in_area`) и границы области; `_all_stat.dat` — по строке на
область: высота мм, нижний край мм, число траекторий, средние vx, vy, r.

**`FrameResult`** (окно просмотра): `clean` (кадр без рима, uint8), `erased` (маска закрашенного),
`centers`, `radii`, `piv` (после фильтра), `pair` (номера файлов пары PIV), `xd`, `yd`.

## 6. Тесты — [tests/](tests/)

| Файл | Что проверяет |
|---|---|
| [test_functions.py](tests/test_functions.py) | отдельные функции на маленьких примерах: сортировка кадров, формат .dat, таблица записей, нумерация объектов, `disk`, медиана, фильтр PIV, полином, пересечение отрезков, единицы, ручные области |
| [test_synthetic.py](tests/test_synthetic.py) | искусственная съёмка с известными каплями: st1 → st2 → st4 восстанавливают положения, радиусы, скорости; окно просмотра (виды, слайдшоу) и совпадение его расчёта с st1 |
| [test_docs.py](tests/test_docs.py) | описания у всех функций; полнота таблиц этого файла |
| [compare_with_matlab.py](tests/compare_with_matlab.py) | не тест, а отчёт: насколько результаты совпадают с результатами MATLAB |

## 7. Как расширять

- **Новый эксперимент** — скопировать папку в [experiments/](experiments/), поправить `settings.py`
  и `thresholds.py`, выбрать её в `current.py`. Код не меняется.
- **Новый порог** — добавить в `thresholds.py` всех экспериментов, использовать в `from_thresholds`
  нужного класса (или в `process_record`), добавить строку в раздел 4 этого файла.
- **Новый способ просмотра st1** — короткая оболочка над `RecordPreview` и `ViewDrawer`
  ([lib/st1/preview.py](lib/st1/preview.py)), по образцу `Viewer` ([tools/st1_viewer.py](tools/st1_viewer.py)).
- **Новая функция в `lib/`** — описание в коде и строка в разделе 3; `test_docs.py` напомнит, если забыть.
- **Исправление «вопроса к специалисту»** — только после решения пользователя; замеры и варианты —
  [QUESTIONS.md](QUESTIONS.md).
