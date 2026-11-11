"""Общая отмена приложения: одна история на все правки — таблица записей и файлы настроек.

Свои шаги помнят сами части: таблица — lib/common/table_edit.py (undo_stack), панель настроек —
app/params_page.py (history). Здесь — их общий порядок: каждая часть после новой правки сообщает о шаге
(record), и Ctrl+Z / Ctrl+Y отменяют последнюю правку в том порядке, в каком их делали, чем бы они ни были.
Если часть забыла свою историю (сохранение, «вернуть как в файлах», смена эксперимента), её шаги
убираются (forget) — отменять их уже нельзя, и Ctrl+Z не должен перескакивать через них.
"""

from typing import Callable

# Шаг: откуда (таблица или панель настроек), что отменяется словами, отменить, повторить
Step = tuple[object, str, Callable[[], bool], Callable[[], bool]]


class UndoManager:
    """История отмены приложения: шаги разных частей окна в общем порядке.

    record — записать новый шаг после правки; undo / redo — отменить / повторить последний шаг
    (возвращают текст шага или None); forget — убрать шаги части; clear — забыть всё.
    """

    def __init__(self) -> None:
        self.undo_steps: list[Step] = []  # последний шаг — в конце
        self.redo_steps: list[Step] = []

    def record(self, provider: object, text: str, undo: Callable[[], bool], redo: Callable[[], bool]) -> None:
        """Записать новый шаг отмены после правки provider; повторять больше нечего."""
        self.undo_steps.append((provider, text, undo, redo))
        self.redo_steps.clear()

    def undo(self) -> str | None:
        """Отменить последний шаг, который ещё можно отменить; None — отменять нечего."""
        while self.undo_steps:
            provider, text, undo, redo = self.undo_steps.pop()
            if undo():
                self.redo_steps.append((provider, text, undo, redo))
                return text
        return None

    def redo(self) -> str | None:
        """Повторить последний отменённый шаг; None — повторять нечего."""
        while self.redo_steps:
            provider, text, undo, redo = self.redo_steps.pop()
            if redo():
                self.undo_steps.append((provider, text, undo, redo))
                return text
        return None

    def forget(self, provider: object) -> None:
        """Убрать шаги части: её история очищена, отменять их нельзя."""
        if provider is None:
            return
        self.undo_steps = [step for step in self.undo_steps if step[0] is not provider]
        self.redo_steps = [step for step in self.redo_steps if step[0] is not provider]

    def clear(self) -> None:
        """Забыть всё (например, сменился эксперимент)."""
        self.undo_steps, self.redo_steps = [], []
