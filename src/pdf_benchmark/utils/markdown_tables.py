from __future__ import annotations

import re

from pdf_benchmark.models import TableCell


def _split_row(line: str) -> list[str]:
    """Split pipe delimiters, retaining empty cells and non-delimiter escapes."""
    line = line.strip()
    cells: list[str] = []
    current: list[str] = []
    backslashes = 0
    ends_with_delimiter = False
    for char in line:
        ends_with_delimiter = False
        if char == "|":
            if backslashes % 2:
                current.pop()  # Remove only the escape for a literal pipe.
                current.append(char)
            else:
                cells.append("".join(current).strip())
                current = []
                ends_with_delimiter = True
            backslashes = 0
        else:
            current.append(char)
            backslashes = backslashes + 1 if char == "\\" else 0
    cells.append("".join(current).strip())
    # Strip one optional border, not every pipe/empty cell at the edges.
    if line.startswith("|"):
        cells.pop(0)
    if ends_with_delimiter:
        cells.pop()
    return cells


def parse_markdown_table(markdown: str) -> tuple[int, int, list[TableCell]]:
    """Parse the first pipe table with a header and matching separator row.

    Cell text retains native inline markup, as in the existing Markdown adapter
    path. No missing cells, merged spans or corrected OCR values are inferred.
    The caller should also preserve the source Markdown for inspection.
    """
    lines = markdown.splitlines()
    for index in range(len(lines) - 1):
        if "|" not in lines[index]:
            continue
        header = _split_row(lines[index])
        separator = _split_row(lines[index + 1])
        if not header or len(header) != len(separator):
            continue
        if not all(re.fullmatch(r":?-{3,}:?", value) for value in separator):
            continue
        parsed = [header]
        for line in lines[index + 2:]:
            if not line.strip() or "|" not in line:
                break
            parsed.append(_split_row(line))
        cells = [
            TableCell(row_index=r, column_index=c, text=text, is_header=(r == 0))
            for r, row in enumerate(parsed)
            for c, text in enumerate(row)
        ]
        return len(parsed), max(len(row) for row in parsed), cells
    return 0, 0, []
