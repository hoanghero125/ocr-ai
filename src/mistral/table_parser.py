"""Parse HTML and markdown tables from Mistral OCR output into ExtractedTable objects."""

import re
from html.parser import HTMLParser

from src.models.result import ExtractedTable

# Matches a markdown pipe table row: | cell | cell |
_MD_ROW_RE = re.compile(r"^\|(.+)\|$")
# Matches a separator row: |---|:---:|---| (all dashes, colons, pipes, spaces)
_MD_SEP_RE = re.compile(r"^\|[\s\-:|]+\|$")
# Matches a full <table>...</table> block (non-greedy, DOTALL)
_HTML_TABLE_RE = re.compile(r"<table[\s\S]*?</table>", re.IGNORECASE)


# ── HTML parsing ────────────────────────────────────────────────────────────


class _TableParser(HTMLParser):
    """Extract all tables from an HTML fragment."""

    def __init__(self) -> None:
        super().__init__()
        self._tables: list[list[list[str]]] = []
        self._current_table: list[list[str]] = []
        self._current_row: list[str] = []
        self._current_cell: str = ""
        self._in_cell = False

    def handle_starttag(self, tag: str, attrs: list) -> None:
        tag = tag.lower()
        if tag == "table":
            self._current_table = []
        elif tag == "tr":
            self._current_row = []
        elif tag in ("td", "th"):
            self._current_cell = ""
            self._in_cell = True

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in ("td", "th"):
            self._current_row.append(self._current_cell.strip())
            self._in_cell = False
        elif tag == "tr":
            if self._current_row:
                self._current_table.append(self._current_row)
        elif tag == "table":
            if self._current_table:
                self._tables.append(self._current_table)

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._current_cell += data

    @property
    def tables(self) -> list[list[list[str]]]:
        return self._tables


def _parse_html_tables(text: str) -> list[ExtractedTable]:
    results: list[ExtractedTable] = []
    for match in _HTML_TABLE_RE.finditer(text):
        raw = match.group(0)
        parser = _TableParser()
        parser.feed(raw)
        for grid in parser.tables:
            if not grid:
                continue
            headers = grid[0]
            rows = grid[1:]
            results.append(ExtractedTable(headers=headers, rows=rows, raw=raw))
    return results


# ── Markdown parsing ─────────────────────────────────────────────────────────


def _split_md_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _parse_markdown_tables(text: str) -> list[ExtractedTable]:
    results: list[ExtractedTable] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        if not _MD_ROW_RE.match(line):
            i += 1
            continue
        # Potential header row — next non-empty line must be a separator
        if i + 1 >= len(lines) or not _MD_SEP_RE.match(lines[i + 1].rstrip()):
            i += 1
            continue

        headers = _split_md_row(line)
        raw_lines = [line, lines[i + 1]]
        rows: list[list[str]] = []
        j = i + 2
        while j < len(lines) and _MD_ROW_RE.match(lines[j].rstrip()):
            raw_lines.append(lines[j])
            rows.append(_split_md_row(lines[j]))
            j += 1

        results.append(
            ExtractedTable(headers=headers, rows=rows, raw="\n".join(raw_lines))
        )
        i = j
    return results


# ── Public API ───────────────────────────────────────────────────────────────


def parse_tables(page_text: str) -> list[ExtractedTable]:
    """
    Extract all tables from a single OCR page string.
    HTML tables take priority; markdown tables are parsed from the remainder.
    Returns an empty list if no tables are found.
    """
    html_tables = _parse_html_tables(page_text)

    # Remove HTML table blocks before scanning for markdown tables
    remainder = _HTML_TABLE_RE.sub("", page_text)
    md_tables = _parse_markdown_tables(remainder)

    return html_tables + md_tables
