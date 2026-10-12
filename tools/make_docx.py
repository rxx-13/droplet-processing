r"""Word-версии документации: README.md, METHOD.md, CODE_GUIDE.md, QUESTIONS.md -> папка docx/.

Конвертирует pandoc'ом (ставится вместе с проектом: `uv sync`, пакет pypandoc-binary).
Формулы ($…$) становятся формулами Word, таблицы — таблицами. Ссылки на другие документы ведут
на их .docx в той же папке, ссылки на файлы кода — на файлы проекта (на уровень выше папки docx/).

После правки .md запустить заново — .docx перезапишутся:
    .venv\Scripts\python.exe tools\make_docx.py
"""

import sys
from pathlib import Path

sys.dont_write_bytecode = True  # не создавать папки __pycache__

import re  # noqa: E402

import pypandoc  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docx"
DOCUMENTS = ["README.md", "METHOD.md", "CODE_GUIDE.md", "QUESTIONS.md"]
MARKDOWN = "gfm+tex_math_dollars"  # как пишет GitHub/VS Code, плюс формулы в $…$


def fix_links(text: str) -> str:
    """Ссылки для документа в папке docx/: на другой документ — его .docx, на файл проекта — ../путь."""

    def replace(match: re.Match) -> str:
        target = match.group(1)
        if re.match(r"[a-z]+://|#|mailto:", target):
            return match.group(0)  # внешняя ссылка или ссылка внутри документа
        if target in DOCUMENTS:
            return f"]({Path(target).stem}.docx)"
        return f"](../{target})"

    return re.sub(r"\]\(([^)\s]+)\)", replace, text)


def convert(name: str) -> Path:
    """Один .md из корня проекта -> docx/<имя>.docx."""
    source = (ROOT / name).read_text(encoding="utf-8")
    target = OUT / f"{Path(name).stem}.docx"
    pypandoc.convert_text(fix_links(source), "docx", format=MARKDOWN, outputfile=str(target))
    return target


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    for document in DOCUMENTS:
        print("готово:", convert(document).relative_to(ROOT))
