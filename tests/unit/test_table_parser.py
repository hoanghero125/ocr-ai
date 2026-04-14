"""Unit tests for HTML and markdown table parsing."""

import pytest

from src.mistral.table_parser import parse_tables


def test_html_table_parses_correctly():
    html = """
    <table>
      <tr><th>Name</th><th>Age</th></tr>
      <tr><td>Alice</td><td>30</td></tr>
      <tr><td>Bob</td><td>25</td></tr>
    </table>
    """
    tables = parse_tables(html)
    assert len(tables) == 1
    assert tables[0].headers == ["Name", "Age"]
    assert tables[0].rows == [["Alice", "30"], ["Bob", "25"]]
    assert "<table" in tables[0].raw


def test_markdown_table_parses_correctly():
    md = """
| Name  | Age |
|-------|-----|
| Alice | 30  |
| Bob   | 25  |
"""
    tables = parse_tables(md)
    assert len(tables) == 1
    assert tables[0].headers == ["Name", "Age"]
    assert tables[0].rows == [["Alice", "30"], ["Bob", "25"]]


def test_page_with_both_types_returns_both():
    content = """
<table>
  <tr><th>Col1</th><th>Col2</th></tr>
  <tr><td>A</td><td>B</td></tr>
</table>

Some text in between.

| X | Y |
|---|---|
| 1 | 2 |
"""
    tables = parse_tables(content)
    assert len(tables) == 2
    html_table = tables[0]
    md_table = tables[1]
    assert html_table.headers == ["Col1", "Col2"]
    assert md_table.headers == ["X", "Y"]


def test_empty_page_returns_empty_list():
    tables = parse_tables("")
    assert tables == []


def test_plain_text_no_tables():
    tables = parse_tables("This is just some plain text with no tables.")
    assert tables == []


def test_markdown_table_without_separator_not_parsed():
    # Missing separator row — should not be recognised as a table
    md = "| Name | Age |\n| Alice | 30 |"
    tables = parse_tables(md)
    assert tables == []


def test_html_table_raw_preserved():
    html = "<table><tr><th>A</th></tr><tr><td>1</td></tr></table>"
    tables = parse_tables(html)
    assert tables[0].raw == html


def test_multiple_html_tables():
    html = """
    <table><tr><th>A</th></tr><tr><td>1</td></tr></table>
    <table><tr><th>B</th></tr><tr><td>2</td></tr></table>
    """
    tables = parse_tables(html)
    assert len(tables) == 2
    assert tables[0].headers == ["A"]
    assert tables[1].headers == ["B"]
