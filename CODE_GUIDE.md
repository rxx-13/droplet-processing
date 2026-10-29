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
Приложение ([app/](app/)) — окно над теми же функциями `lib/`: просмотр кадров и заполнение таблицы записей.

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

### Приложение — [app/](app/)

```
run.py → MainWindow (window.py)                  две части: «Обработка измерений» (выбор эксперимента,
│                                                «Сделать текущим», вкладки ниже, Ctrl+S, F5) и «Обработка результатов»
│     current_experiment, set_current_experiment (lib/common/config_edit.py) — experiments/current.py
├─ RecordsPage (records_page.py)                 вкладка «Записи»
│   ├─ EditableTable (lib/common/table_edit.py)  таблица: все колонки, правки в памяти, автозаполнение
│   │     read_table; record (как read_records); suggestion, fill_like_previous;
│   │     save → save_cells (только изменённые ячейки; копия таблицы — в <OUTPUT_DIR>/table_backups)
│   ├─ Frames                                    все кадры записи (не только диапазон)
│   ├─ FrameComputer (frame_view.py)             расчёт кадров в процессах: текущий — в своём, впрок — в остальных
│   │     compute_frame (lib/st1/preview.py)     st1 для одного кадра — тот же, что у окна просмотра
│   ├─ FrameView (frame_view.py)                 показ видов 1–5 (pyqtgraph), щелчок по кадру (центр затравки)
│   ├─ FrameScale (frame_view.py)                шкала кадров с подсветкой диапазона
│   ├─ SavedResults (lib/st1/preview.py)        «Капли и PIV: взять из результатов st1» — виды 3–5 без пересчёта
│   └─ ParamsPanel (params_page.py)              панель «Настройки» справа: значения settings.py, thresholds.py
│         SettingsFile (lib/common/config_edit.py) × 2: правки в памяти; working → расчёт кадра
│         (apply_parameters); save — только изменённые значения, комментарии остаются
├─ JobsPage (jobs_page.py)                       вкладка «Запуск»: отмеченные записи, стадии st1 → st2 → st4
│     JobRunner (lib/common/jobs.py)             по процессу на запись; в процессе run_job → process_record из main/
│     сообщения процессов (ход работы, итог)     → колонка «Ход», общий лог, счётчики
├─ ResultsPage (results_page.py)                 вкладка «Результаты»
│     draw_tracks ← ptv_unfiltered.mat; _all_stat.dat; draw_area ← tracks_in_area, границы области из её .mat
└─ AnalysisPage (analysis_page.py)               часть «Обработка результатов» (от эксперимента не зависит)
      find_sources, load (lib/analysis/dataset.py) ← итоговые .mat / .dat st4 + параметры записей из таблиц
      computed_columns (lib/analysis/formulas.py)    свои колонки «имя = формула»
      ConditionRow → Condition.mask; window_edges; split (графики рядом → цвет → маркер) → histogram / binned /
      points (lib/analysis/stats.py) → Drawn (кривая: график, подпись, вид) → _draw; список кривых, сводка
      fit, evaluate (lib/analysis/formulas.py)       своя кривая и подгонка параметров
      write_csv, write_lml (lib/analysis/labplot.py)  экспорт в CSV и проект LabPlot; state / set_state — настройки в .json
```
«Что показывать» — `compute_frame` из `preview.py`, «как показывать» — `FrameView`. Таблица xlsx
и файлы настроек меняются только по «Сохранить».

## 3. Функции по файлам

Колонки: что делает; кто вызывает; что использует; аналог в MATLAB (если есть).

### [lib/common/files.py](lib/common/files.py) — кадры, .mat, .dat

| Функция | Что делает | Кто вызывает | Использует | MATLAB |
|---|---|---|---|---|
| `last_number_in_name` | последнее число в имени файла | `Frames` | — | ExtractNumber |
| `Frames` | кадры записи по порядку номеров, размер кадра | main/st1, `RecordPreview`, `_frames`, `RecordsPage` | `last_number_in_name` | imLoader_anyext, FilesInFolder |
| `Frames.gray` | кадр № (с 1) как массив float64 | main/st1, `RecordPreview.frame`, `compute_frame`, `_background`, `RecordsPage` | — | getImageDoubleGray |
| `to_cell` | список → ячейка MATLAB N×1 | main/st1, `save_tracks` | — | cell |
| `load_cell` | ячейка из .mat → список | `load_particles`, main/st4, `ResultsPage`, tests | — | load |
| `save_mat` | запись .mat | main/st1, main/st4, `save_tracks` | — | save |
| `load_particles` | капли из particles.mat по кадрам | main/st2, `RecordPreview._load_matlab`, `RecordsPage`, `SavedResults`, tests | `load_cell` | — |
| `_dat_number` | число в формате %16.7e | `save_dat` | — | — |
| `save_dat` | таблица в .dat как save -ascii | main/st4 | `_dat_number` | save -ascii |
| `load_dat` | .dat → таблица | `ResultsPage`, tests | — | load |

### [lib/common/records.py](lib/common/records.py) — таблица записей и цикл стадии

| Функция | Что делает | Кто вызывает | Использует | MATLAB |
|---|---|---|---|---|
| `project_path` | путь из settings.py: полный — как есть, относительный — от папки проекта | `Record.images_dir`, `Record.st1_dir`, `Record.st2_dir`, `Record.st4_dir`, `read_records`, `EditableTable`, `RecordsPage`, tests | — | — |
| `Record` | одна строка таблицы | `read_records`, `EditableTable.record` | — | строка rec_list |
| `Record.has_frames` | задан ли диапазон кадров | `run_stage`, `Viewer`, `EditableTable.filled`, `RecordsPage`, tests | — | — |
| `Record.images_dir` | папка кадров записи (IMAGES_DIR — полный или от папки проекта) | main/st1, `RecordPreview`, `RecordsPage` | — | fullfile(images_folder_base, …) |
| `Record.st1_dir` | папка результата st1 (OUTPUT_DIR — полный или от папки проекта) | main/st1, main/st2, `RecordPreview._load_matlab`, `RecordsPage`, `ResultsPage`, tests | — | outputFolder st1 |
| `Record.st2_dir` | папка результата st2 | main/st2, main/st4, `RecordsPage`, `ResultsPage`, tests | — | outputFolder st2 |
| `Record.st4_dir` | папка результата st4 | main/st4, `RecordsPage`, `ResultsPage`, tests | — | outputFolder st4 |
| `_text` | ячейка → текст | `read_records`, `read_table`, `save_cells`, `EditableTable` | — | — |
| `_number` | ячейка → число или None | `read_records`, `_integer`, `EditableTable.record` | — | — |
| `_integer` | ячейка → целое или None | `read_records`, `EditableTable.record` | `_number` | — |
| `read_records` | записи из xlsx (путь — полный или от папки проекта) с выбором по условиям и именам | `run_stage`, `Viewer`, tests | `Record`, `_text`, `_number`, `_integer` | My_importfile_xls_with_headers_to_table_v1 |
| `run_stage` | цикл стадии по записям, лог, код выхода | main/st1, main/st2, main/st4 | `read_records`, `process_record` стадии | цикл по rr_i |

### [lib/common/table_edit.py](lib/common/table_edit.py) — таблица записей для приложения

| Функция | Что делает | Кто вызывает | Использует | MATLAB |
|---|---|---|---|---|
| `TableSaveError` | таблицу не удалось записать (файл не изменён) | `save_cells`, `RecordsPage.save` | — | — |
| `TableRow` | строка данных: номер строки Excel и значения по заголовкам | `read_table` | — | — |
| `is_empty` | пустая ли ячейка | `same_value`, `EditableTable` | — | — |
| `same_value` | одинаковы ли значения ячеек (200 = 200.0) | `EditableTable`, `RecordsPage` | `is_empty` | — |
| `format_value` | значение ячейки → текст поля ввода | `RecordsPage` | — | — |
| `read_table` | все колонки (заголовки, описания) и строки с rec_name | `EditableTable` | `TableRow`, `_text` | — |
| `save_cells` | запись изменённых ячеек в xlsx (остальное не трогается) и новых колонок | `EditableTable.save` | `_text` | — |
| `EditableTable` | таблица с несохранёнными правками | `RecordsPage`, tests | `read_table` | — |
| `EditableTable.column` | заголовок колонки величины (rim или rim_new, cx или c_x …) | `EditableTable`, `RecordsPage` | `COLUMN_NAMES` | — |
| `EditableTable.add_column` | добавить колонку, которой нет в таблице (в файл — при save) | `RecordsPage._bag_click`, tests | — | — |
| `EditableTable.original` | значение ячейки в файле | `EditableTable` | — | — |
| `EditableTable.value` | значение ячейки с учётом правок | `EditableTable`, `RecordsPage` | — | — |
| `EditableTable.set` | правка ячейки | `EditableTable.fill_like_previous`, `RecordsPage.set_value` | `same_value` | — |
| `EditableTable.changed` | есть ли правка в строке или ячейке | `RecordsPage` | — | — |
| `EditableTable.name` | rec_name строки | `RecordsPage` | — | — |
| `EditableTable.is_text` | текстовая ли колонка | `EditableTable.parse` | — | — |
| `EditableTable.is_numeric` | числовая ли колонка | `EditableTable.parse` | — | — |
| `EditableTable.parse` | текст поля → значение ячейки (число, текст, пусто) | `RecordsPage` | `EditableTable.is_text`, `EditableTable.is_numeric` | — |
| `EditableTable.record` | строка как `Record` (те же правила, что `read_records`) | `EditableTable`, `RecordsPage` | `Record`, `_text`, `_number`, `_integer` | — |
| `EditableTable.filled` | заполнена ли запись (задан диапазон кадров) | `EditableTable` | `EditableTable.record` | — |
| `EditableTable.previous_filled` | ближайшая выше (иначе ниже) заполненная запись | `EditableTable.suggestion` | `EditableTable.filled` | — |
| `EditableTable.suggestion` | подсказка для пустого поля | `RecordsPage` | `EditableTable.previous_filled` | — |
| `EditableTable.common_values` | колонки, одинаковые во всех заполненных записях | `EditableTable.fill_like_previous`, `RecordsPage` | `same_value` | — |
| `EditableTable.fill_like_previous` | «как у предыдущих»: пустые поля — общими значениями | `RecordsPage.fill_like_previous` | `EditableTable.common_values` | — |
| `EditableTable.save` | копия таблицы, запись правок, перечитывание | `RecordsPage.save` | `save_cells`, `read_table` | — |

### [lib/common/config_edit.py](lib/common/config_edit.py) — файлы настроек для приложения

| Функция | Что делает | Кто вызывает | Использует | MATLAB |
|---|---|---|---|---|
| `Setting` | значение файла настроек: имя, текст, значение, комментарии, раздел, место в файле | `parse_settings` | — | — |
| `_char_column` | номер символа в строке по смещению в байтах (так считает ast) | `parse_settings` | — | — |
| `literal` | текст литерала Python → значение (ошибка — понятный текст) | `SettingsFile`, `ParamField`, `ParamsPanel` | — | — |
| `parse_settings` | значения `ИМЯ = литерал` с комментариями и разделами из текста файла | `SettingsFile` | `Setting`, `_char_column` | — |
| `value_text` | значение → текст литерала (строки с «\\» — как r"...") | `ParamField`, tests | — | — |
| `kind_of` | вид значения словами: число, строка, кортеж, список | `SettingsFile.set_text` | — | — |
| `SettingsFile` | файл настроек с несохранёнными правками | `ParamsPanel`, tests | `SettingsFile._read` | — |
| `SettingsFile._read` | прочитать файл (правки забываются) | `SettingsFile`, `SettingsFile.save` | `parse_settings` | — |
| `SettingsFile.text` | текст значения с учётом правок | `SettingsFile`, `ParamsPanel` | — | — |
| `SettingsFile.value` | значение с учётом правок | `SettingsFile`, `ParamsPanel.save`, tests | `literal` | — |
| `SettingsFile.values` | все значения файла с учётом правок | `ParamsPanel.working` | `SettingsFile.value` | — |
| `SettingsFile.set_text` | правка значения (литерал того же вида, что в файле) | `ParamsPanel`, tests | `literal`, `kind_of` | — |
| `SettingsFile.changed` | есть ли правки (у значения или в файле) | `ParamsPanel` | — | — |
| `SettingsFile.discard` | забыть правки | `ParamsPanel.discard` | — | — |
| `SettingsFile.save` | записать в файл только изменённые значения, перечитать | `ParamsPanel.save`, tests | `parse_settings`, `literal` | — |
| `current_experiment` | эксперимент, выбранный в experiments/current.py | `MainWindow`, tests | — | — |
| `set_current_experiment` | выбрать эксперимент в experiments/current.py (комментарии остаются) | `MainWindow._make_current`, tests | — | — |

### [lib/common/jobs.py](lib/common/jobs.py) — запуск стадий для приложения

| Функция | Что делает | Кто вызывает | Использует | MATLAB |
|---|---|---|---|---|
| `Job` | задание: запись, цепочка стадий, файлы настроек и порогов | `JobsPage.start`, tests | `Record` | — |
| `load_module` | модуль из файла .py (настройки, пороги, скрипт стадии) | `run_job` | — | — |
| `_QueueLog` | строки хода работы (logging) → очередь сообщений приложения | `run_job` | — | — |
| `_QueueLog.emit` | отправить строку в очередь | logging | — | — |
| `run_job` | стадии задания по порядку (в отдельном процессе): `process_record` скриптов main/ | `JobRunner.poll` (новый процесс) | `load_module`, `_QueueLog` | — |
| `JobRunner` | очередь заданий: по процессу на запись, не больше workers сразу | `JobsPage`, tests | — | — |
| `JobRunner.submit` | поставить задания в очередь | `JobsPage.start`, tests | — | — |
| `JobRunner.busy` | есть ли ожидающие или идущие задания | `JobsPage`, tests | — | — |
| `JobRunner._drain` | забрать пришедшие сообщения процессов | `JobRunner.poll`, `JobRunner.stop` | — | — |
| `JobRunner.poll` | запустить ожидающие, забрать сообщения, заметить упавшие процессы | `JobsPage._poll`, tests | `run_job`, `JobRunner._drain` | — |
| `JobRunner.stop` | остановить всё (процессы завершаются) | `JobsPage.stop`, `JobsPage.shutdown` | `JobRunner._drain` | — |

### [lib/analysis/dataset.py](lib/analysis/dataset.py) — данные для «Обработки результатов»

| Функция | Что делает | Кто вызывает | Использует | MATLAB |
|---|---|---|---|---|
| `Source` | результаты одного эксперимента: таблица записей и папка результатов (Python или MATLAB) | `AnalysisPage`, tests | — | — |
| `Source.label` | подпись «эксперимент — результаты Python» | `AnalysisPage` | — | — |
| `sources_of` | источники эксперимента по его settings (результаты MATLAB — только если попросить) | `find_sources` | `Source` | — |
| `find_sources` | источники всех экспериментов папки experiments/ | `AnalysisPage.refresh_sources` | `load_module`, `sources_of` | — |
| `DataTable` | таблица колонок numpy (числа и текст) | `load`, `select`, `AnalysisPage`, tests | — | — |
| `DataTable.label` | подпись колонки для оси (LABELS или описание из таблицы записей) | `AnalysisPage` | — | — |
| `DataTable.names` | имена колонок | `AnalysisPage`, `ConditionRow` | — | — |
| `DataTable.is_numeric` | числовая ли колонка | `Condition.mask`, `split`, `AnalysisPage` | — | — |
| `DataTable.take` | строки по маске | `select`, `split` | — | — |
| `DataTable.unique` | разные значения колонки | `split`, `ConditionRow`, `AnalysisPage` | — | — |
| `DataTable.concat` | таблицы друг под другом | `load`, `AnalysisPage.series_table` | — | — |
| `_track_blocks` | траектории в областях из `<область>.mat` записи | `load` | — | st4: p_in_area_scale |
| `_area_blocks` | области из `_all_stat.dat` записи | `load` | `load_dat` | st4: _all_stat |
| `load` | строки выбранного уровня из результатов st4 источников + параметры записей | `AnalysisPage.load_data`, tests | `EditableTable`, `Record.st4_dir`, `_track_blocks`, `_area_blocks`, `DataTable.concat` | — |

### [lib/analysis/stats.py](lib/analysis/stats.py) — выборка, окна, распределения

| Функция | Что делает | Кто вызывает | Использует | MATLAB |
|---|---|---|---|---|
| `Condition` | условие выборки: равно значениям и/или от–до | `ConditionRow.condition`, tests | — | — |
| `Condition.mask` | какие строки подходят | `select`, `AnalysisPage._select` | `DataTable.is_numeric` | — |
| `select` | строки, подходящие под все условия | tests | `Condition.mask`, `DataTable.take` | — |
| `window_edges` | границы окон: ширина или число, пределы | `AnalysisPage.replot`, tests | — | — |
| `Series` | посчитанная кривая: x, y, разброс, число, окна | `histogram`, `binned`, `points`, `AnalysisPage` | — | — |
| `Series.columns` | таблица кривой для экспорта | `AnalysisPage` | — | — |
| `_window_index` | номер окна каждого значения | `histogram`, `binned` | — | — |
| `histogram` | распределение по окнам: число, доля, плотность | `AnalysisPage._split`, tests | `_window_index` | — |
| `binned` | статистика y по окнам x: среднее ± СКО, ± ошибка среднего, медиана, число | `AnalysisPage._split`, tests | `_window_index` | — |
| `points` | точки (x, y) без NaN | `AnalysisPage._split` | — | — |
| `label_value` | значение для подписи (целые — без «.0») | `split`, `ConditionRow` | — | — |
| `split` | серии по значениям колонки | `AnalysisPage._split` (цвет, маркер, графики рядом), tests | `DataTable.unique`, `DataTable.take` | — |

### [lib/analysis/formulas.py](lib/analysis/formulas.py) — свои формулы и подгонка

| Функция | Что делает | Кто вызывает | Использует | MATLAB |
|---|---|---|---|---|
| `parse` | проверка формулы: только арифметика и функции FUNCTIONS | `names_in`, `evaluate` | — | — |
| `names_in` | имена в формуле в порядке написания | `parameters_of`, `evaluate`, tests | `parse` | — |
| `evaluate` | значение формулы по колонкам и параметрам | `fit`, `computed_columns`, `AnalysisPage._curves`, tests | `parse`, `names_in` | — |
| `parameters_of` | параметры кривой (всё, кроме x и функций) | `fit`, `AnalysisPage._fill_parameters`, tests | `names_in` | — |
| `FitResult` | итог подгонки: параметры, погрешности, R², число точек | `fit`, `AnalysisPage` | — | — |
| `fit` | подгонка параметров кривой методом наименьших квадратов | `AnalysisPage._curves`, tests | `parameters_of`, `evaluate`, scipy `curve_fit` | — |
| `computed_columns` | свои колонки по строкам «имя = формула» | `AnalysisPage._apply_columns`, tests | `evaluate` | — |

### [lib/analysis/labplot.py](lib/analysis/labplot.py) — экспорт в CSV и LabPlot

| Функция | Что делает | Кто вызывает | Использует | MATLAB |
|---|---|---|---|---|
| `write_csv` | таблица в CSV | `AnalysisPage.export`, tests | — | — |
| `_now` | время в записи LabPlot | `_filters`, `_column`, `write_lml` | — | — |
| `_filters` | фильтры ввода и вывода колонки LabPlot | `_column` | `_now` | — |
| `_column` | колонка проекта LabPlot (числа base64 или текст по строкам) | `write_lml` | `_filters`, `_now` | — |
| `write_lml` | проект LabPlot (.lml, xz) с таблицами | `AnalysisPage.export`, tests | `_column`, `_now` | — |

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
| `PivGrid` | узлы сетки PIV | `Piv`, `_load_piv` | — | PIV_Grid |
| `PivGrid.regular` | регулярная сетка с шагом | `Piv` | — | PIV_Grid(0:step:W, 0:step:H) |
| `PivGrid.shape` | (nx, ny) | `Piv.compute` | — | — |
| `PivGrid.x_mat` | x узлов, (nx, ny) | `velocity_at_points`, `ViewDrawer.show`, `FrameView.show`, `PivGrid.to_struct` | — | xMat |
| `PivGrid.y_mat` | y узлов, (nx, ny) | `velocity_at_points`, `ViewDrawer.show`, `FrameView.show`, `PivGrid.to_struct` | — | yMat |
| `PivGrid.to_struct` | поля PIV_Grid для .mat | `Piv` | — | — |
| `PivResult` | результат PIV пары кадров | `Piv.compute`, `filter_piv`, `_load_piv` | — | PIV_output |
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
| `FrameJob` | задание на расчёт одного кадра | `RecordPreview`, `FrameComputer` | — | — |
| `FrameResult` | результат расчёта кадра | `compute_frame`, `SavedResults.result` | — | — |
| `FrameResult.erased_mask` | маска закрашенного | `ViewDrawer.show`, `FrameView.show` | — | — |
| `_frames` | кадры записи, запоминаются в процессе | `compute_frame`, `_background` | `Frames` | — |
| `_background` | кадр фона, запоминается | `compute_frame` | `_frames` | — |
| `compute_frame` | st1 для одного кадра | `RecordPreview.result`, `RecordPreview.submit_all`, `FrameComputer` | `RimRemover`, `DropletFinder`, `Piv`, `filter_piv_by_thresholds`, `velocity_at_points` | st1 для кадра |
| `_load_piv` | PIV после фильтрации из PIV_output_filtered_arr.mat по парам кадров | `SavedResults` | `PivGrid`, `PivResult` | — |
| `SavedResults` | готовые результаты st1 записи (капли, PIV) — показ без пересчёта | `RecordsPage._saved_at` | `load_particles`, `_load_piv` | — |
| `SavedResults.result` | кадр диапазона из готовых результатов как `FrameResult` | `RecordsPage._saved_at` | `FrameResult` | — |
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
| `draw_tracks` | траектории на оси | `plot_tracks`, `ResultsPage` | — | plot |
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
| `Area` | прямоугольная область поиска, px | main/st4, `ResultsPage` | — | search_area_x, search_area_y |
| `px_to_mm` | положение px → мм от (cx, cy) | main/st4, `draw_area` | — | px2mm |
| `px_per_frame_to_m_per_s` | px/кадр → м/с | main/st4 | — | vpx2vmps |
| `segments_intersect` | пересечения отрезков | `tracks_in_area` | — | lineSegmentIntersect |
| `tracks_in_area` | средние по траекториям в области | main/st4, `ResultsPage` | `segments_intersect` | st4_…_v1_function2 |
| `area_file_name` | имя файла области | main/st4, `ResultsPage` | — | sprintf имени |
| `draw_area` | картинка области на двух осях | `plot_area`, `ResultsPage` | `px_to_mm` | plot, quiver |
| `plot_area` | картинка области в файл | main/st4 | `draw_area` | saveas |

### [main/](main/), [tools/](tools/) и [app/](app/)

- `process_record` в каждом скрипте `main/` — обработка одной записи стадии (деревья — в разделе 2);
  вызывается из `run_stage`, а в тестах — напрямую.
- [tools/st1_viewer.py](tools/st1_viewer.py): `Viewer` — окно; методы `press`, `release` (клавиши),
  `tick` (таймер: фоновые результаты и слайдшоу), `draw` и `_title` (рисование и заголовок),
  `_open`, `_switch`, `_reload` (открыть запись, сменить запись, перечитать настройки).
- [tools/make_docx.py](tools/make_docx.py): Word-версии документации в папку `docx/` (pandoc);
  `fix_links` переписывает ссылки для документа в подпапке, `convert` — один документ.
- [app/](app/) — приложение (PySide6 + pyqtgraph), дерево вызовов — в разделе 2:
  - [run.py](app/run.py): `main` — запуск окна;
  - [window.py](app/window.py): `MainWindow` — две части окна; выбор эксперимента (`experiment_names`, `experiment_name`,
    `_choose_experiment`, `_make_current` — запись в experiments/current.py), вкладки (`_tab_changed`), `save` (Ctrl+S — таблица
    и настройки), `reload` (F5), вопрос о несохранённых правках (`_confirm_discard`, `closeEvent`);
  - [records_page.py](app/records_page.py): `RecordsPage` — вкладка «Записи»: список (`_fill_list`, `_update_row`),
    кадры (`open_record`, `draw`, `go`, `step`, `set_view`, слайдшоу), поля (`Field`, `_fill_form`, `set_value`,
    `fill_like_previous`; ползунок rim — `rim_to_slider`, `slider_to_rim`), пересчёт после правки rim
    (`recompute`), правки панели «Настройки» (`apply_parameters`), «Капли и PIV»: считать или из результатов
    st1 (`_source_changed`, `_computes`, `_saved_at`), размер бэга (`_bag_mode`, `_bag_click`, `_bag_text`),
    запись таблицы (`save`);
  - [params_page.py](app/params_page.py): `ParamsPanel` — панель «Настройки» (`load`, `working`, `set_text`,
    `save`, `discard`, поиск — `_filter`); `ParamField` — поле значения с ползунками (`slider_range`,
    `number_text`, `_numbers`);
  - [jobs_page.py](app/jobs_page.py): `CheckPainter` — флажки в списках как выделение в проводнике (щелчок по
    строке, протягивание мышью, Shift — диапазон; и в списке экспериментов «Обработки результатов»);
    `JobsPage` — вкладка «Запуск» (`refresh`, `set_checked`, `start`, `stop`,
    разбор сообщений процессов — `_poll`, `_handle`, итог — `_finish`);
  - [results_page.py](app/results_page.py): `ResultsPage` — вкладка «Результаты» (`refresh`, `show_record`,
    `_draw_tracks`, `_fill_summary`, `_area_selected`, `open_folder`);
  - [analysis_page.py](app/analysis_page.py): `AnalysisPage` — часть «Обработка результатов», раскладка как
    в LabPlot (`_build`, `_panel`): слева данные, в середине сводка (`_summary`), график и таблица, справа свойства;
    разделы — `Section` (сворачиваются). Источники (`refresh_sources`, `checked_sources`, `set_checked`), загрузка
    (`load_data`, `_fill_columns`), свои колонки (`_apply_columns`), выборка (`ConditionRow` с числом оставшихся
    строк, `add_condition`, `_select`), кривые (`_split` → `Drawn`: график, подпись, номера цвета и маркера;
    вид — `_look`, правки — `styles`, `set_style`, `reset_styles`, список — `_fill_curve_table`,
    `_curve_item_changed`, `_pick_color`), график (`replot`, `_draw`, `_decorate`, `_legend`, `_finish`; размеры —
    `_style`, `_colors`), своя кривая и подгонка (`_fill_parameters`, `initial_values`, `_curves`), экспорт
    (`export`, `series_table`, `fit_table`, `_export_dialog`), настройки в файл (`state`, `set_state`; старое
    `series_by` читается как `color_by`);
  - [frame_view.py](app/frame_view.py): `FrameComputer` — расчёт кадров в процессах (`configure`, `focus`,
    `collect`, `result`); `FrameView` — показ видов 1–5 (`show`), крестик центра (`set_center`), щелчок по кадру
    (сигнал `clicked`); `FrameScale` — шкала кадров.

## 4. Настройки и пороги

Настройки эксперимента — `experiments/<эксперимент>/settings.py`, пороги — `thresholds.py` там же,
значения отдельных записей — таблица xlsx. Все значения `settings.py` и `thresholds.py` правятся и в приложении
(панель «Настройки», `ParamsPanel`): с предпросмотром кадра, запись — только изменённых значений.

| Имя | Где задано | Кто использует | На что влияет |
|---|---|---|---|
| `IMAGES_DIR` | settings | main/st1, `RecordPreview`, `RecordsPage` | где искать кадры; этот и другие пути — полные или от папки проекта (`experiments\example1\results`) |
| `TABLE_FILE` | settings | `run_stage`, `Viewer`, `RecordsPage`, `sources_of`, tests | таблица записей |
| `OUTPUT_DIR` | settings | main/st1, main/st2, main/st4, `RecordsPage`, `sources_of` | куда писать результаты; приложение — отметки st1/st2/st4 и копии таблицы (`table_backups`) |
| `CONDITIONS` | settings | `run_stage`, `Viewer` | какие условия обрабатывать |
| `RECORDS` | settings | `run_stage`, `Viewer` | какие записи обрабатывать |
| `SCALE_MM_PER_PX` | settings | main/st4, `RecordPreview`, `RecordsPage`, `ResultsPage` | перевод px → мм, px/кадр → м/с |
| `FPS` | settings | main/st4, `RecordPreview`, `RecordsPage` | перевод px/кадр → м/с |
| `SURFACE_ROW` | settings | `RimRemover` (main/st1, `compute_frame`) | откуда вниз закрашивается вода |
| `BACKGROUND_FRAME` | settings | main/st1, `compute_frame` | какой кадр — фон |
| `ERASE_REGIONS` | settings | `RimRemover` (main/st1, `compute_frame`) | вручную закрашиваемые области |
| `AREA_X` | settings | main/st4 | границы областей по x |
| `AREA_HEIGHTS` | settings | main/st4 | высоты областей |
| `AREA_LOW_EDGES` | settings | main/st4 | положения нижних краёв областей |
| `MATLAB_RESULTS_DIR` | settings | `RecordPreview`, `RecordsPage`, `sources_of`, tests/compare_with_matlab.py | где результаты MATLAB для сравнения (и для «Обработки результатов») |
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
| `TRACKS_PLOT_MIN_POINTS` | thresholds | main/st2 → `plot_tracks`, `ResultsPage` | граница «длинная/короткая» на tracks.png |
| `AREA_PLOT_ARROW_SCALE` | thresholds | main/st4 → `plot_area`, `ResultsPage` | длина стрелок на картинках st4 |
| `AREA_PLOT_Y_LIMITS_MM` | thresholds | main/st4 → `plot_area`, `ResultsPage` | пределы по y на картинках st4 |
| `PIV_PLOT_ARROW_SCALE` | thresholds | `RecordPreview` → `ViewDrawer.show`, `RecordsPage` → `FrameView.show` | длина стрелок PIV в окне просмотра и приложении |
| `PIV_PLOT_SPEED_MAX` | thresholds | `RecordPreview` → `ViewDrawer.show`, `RecordsPage` → `FrameView.show` | верх цветовой шкалы скорости в окне и приложении |
| `rim` / `rim_new` | таблица xlsx | `RimRemover.remove`; правится в приложении | порог площади рима и крупных брызг, px² |
| `start_frame`, `fin_frame` | таблица xlsx | main/st1, `RecordPreview`; правится в приложении | диапазон кадров записи |
| `bag_hight`, `bag_length`, `bag_frame` | таблица xlsx (`COLUMN_NAMES`: bag_height, bag_length, bag_frame) | `RecordsPage` (размер бэга), `load` («Обработка результатов») | высота и длина бэга, мм, и кадр, на котором измерены; стадиям не нужны |
| `cx` / `c_x`, `cy` / `c_y` | таблица xlsx | main/st4; правится в приложении (щелчок «центр затравки») | начало координат в мм |

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
| [test_functions.py](tests/test_functions.py) | отдельные функции на маленьких примерах: сортировка кадров, формат .dat, таблица записей, нумерация объектов, `disk`, медиана, фильтр PIV, полином, пересечение отрезков, единицы, ручные области; таблица для приложения: запись только изменённых ячеек с сохранением форматирования, копия таблицы, автозаполнение, защита от затирания строк, правленных в Excel, и от открытого в Excel файла; файлы настроек: запись только изменённых значений (остальное байт в байт), проверка вида значения, файл, изменённый снаружи, не затирается, выбор эксперимента в current.py; добавление колонки в таблицу; обработка результатов: выборка, окна, гистограмма, статистика по окнам, серии; проект LabPlot и CSV; формулы: защита, порядок параметров, свои колонки, подгонка; пути в settings.py от папки проекта; примеры экспериментов: читаются и дают заложенные зависимости (если их результаты есть на диске) |
| [test_synthetic.py](tests/test_synthetic.py) | искусственная съёмка с известными каплями: st1 → st2 → st4 восстанавливают положения, радиусы, скорости; окно просмотра (виды, слайдшоу) и совпадение его расчёта с st1; приложение без экрана: записи, виды 1–5, слайдшоу, подсказка rim, «начало / конец = этот кадр», центр затравки, сохранение таблицы, капли и PIV из результатов st1 без пересчёта; панель «Настройки»: правка порога пересчитывает кадр, ползунок, ошибка ввода, сохранение в файл, «вернуть как в файлах»; «Запуск»: st1 → st2 → st4 в фоновом процессе дают те же файлы, что скрипты main/, ход работы в окне, «Остановить»; ошибка записи останавливает её цепочку; «Результаты»: траектории, области; размер бэга (четыре щелчка, колонки добавляются в таблицу); «Обработка результатов»: загрузка st4, выборка, графики, экспорт; своя колонка, подгонка, лог-ось, настройки в файл и обратно; флажки: щелчок по строке, протягивание, Shift |
| [test_docs.py](tests/test_docs.py) | описания у всех функций (lib/, main/, tools/, app/); полнота таблиц этого файла |
| [compare_with_matlab.py](tests/compare_with_matlab.py) | не тест, а отчёт: насколько результаты совпадают с результатами MATLAB |

## 7. Как расширять

- **Новый эксперимент** — скопировать пример [experiments/example1/](experiments/example1/) под своим именем,
  поправить `settings.py` и `thresholds.py`, выбрать её в `current.py`. Код не меняется. В git из `experiments/`
  хранятся только настройки и таблицы записей примеров `example1`–`example3` (пути к кадрам — заглушки;
  их синтетические результаты st4 для «Обработки результатов» — только на диске, не в git) и `current.py`,
  который выбирает `example1` (см. `.gitignore`).
- **Новый порог** — добавить в `thresholds.py` всех экспериментов, использовать в `from_thresholds`
  нужного класса (или в `process_record`), добавить строку в раздел 4 этого файла.
- **Новая вкладка приложения** — новый файл в [app/](app/) по образцу `RecordsPage`, вкладка добавляется
  в `MainWindow`; расчёты — функциями из `lib/` (окно их не дублирует).
- **Новый вид графика или статистика в «Обработке результатов»** — функция в
  [lib/analysis/stats.py](lib/analysis/stats.py) (по образцу `binned`), пункт в `KINDS` или `STATISTICS`
  и рисование в `AnalysisPage._draw`. Если изменятся итоговые файлы st4 — править только
  [lib/analysis/dataset.py](lib/analysis/dataset.py).
- **Новый способ просмотра st1** — короткая оболочка над `RecordPreview` и `ViewDrawer`
  ([lib/st1/preview.py](lib/st1/preview.py)), по образцу `Viewer` ([tools/st1_viewer.py](tools/st1_viewer.py)).
- **Новая функция в `lib/`** — описание в коде и строка в разделе 3; `test_docs.py` напомнит, если забыть.
- **Исправление «вопроса к специалисту»** — только после решения пользователя; замеры и варианты —
  [QUESTIONS.md](QUESTIONS.md).
