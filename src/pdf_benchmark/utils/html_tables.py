from __future__ import annotations

from bs4 import BeautifulSoup

from pdf_benchmark.models import TableCell
from pdf_benchmark.utils.text import collapse_ws


def parse_html_table(html: str) -> tuple[int, int, list[TableCell]]:
    """Parse the first HTML table, retaining row/col spans."""
    if not html:
        return 0, 0, []
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if table is None:
        return 0, 0, []

    cells: list[TableCell] = []
    occupied: set[tuple[int, int]] = set()
    max_col = 0
    rows = table.find_all("tr")

    for r_idx, tr in enumerate(rows):
        col = 0
        for tag in tr.find_all(["th", "td"], recursive=False):
            while (r_idx, col) in occupied:
                col += 1
            try:
                rowspan = max(1, int(tag.get("rowspan", 1)))
            except Exception:
                rowspan = 1
            try:
                colspan = max(1, int(tag.get("colspan", 1)))
            except Exception:
                colspan = 1
            text = collapse_ws(tag.get_text(" ", strip=True))
            cells.append(
                TableCell(
                    row_index=r_idx,
                    column_index=col,
                    row_span=rowspan,
                    column_span=colspan,
                    text=text,
                    is_header=(tag.name == "th"),
                )
            )
            for rr in range(r_idx, r_idx + rowspan):
                for cc in range(col, col + colspan):
                    occupied.add((rr, cc))
            max_col = max(max_col, col + colspan)
            col += colspan

    max_row = max((r.row_index + r.row_span for r in cells), default=len(rows))
    return max_row, max_col, cells
