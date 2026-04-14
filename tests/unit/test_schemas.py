"""Unit tests for API request/response validation schemas."""

import pytest
from pydantic import ValidationError

from src.api.schemas import FieldInstructionSchema, ProcessRequest


# ── FieldInstructionSchema — key ──────────────────────────────────────────────

def test_key_valid():
    f = FieldInstructionSchema(key="ho_ten", label="Ho ten")
    assert f.key == "ho_ten"


def test_key_rejects_special_chars():
    with pytest.raises(ValidationError, match="key must match"):
        FieldInstructionSchema(key="ho-ten", label="Ho ten")


def test_key_rejects_spaces():
    with pytest.raises(ValidationError, match="key must match"):
        FieldInstructionSchema(key="ho ten", label="Ho ten")


def test_key_rejects_too_long():
    with pytest.raises(ValidationError, match="50 characters"):
        FieldInstructionSchema(key="a" * 51, label="Label")


# ── FieldInstructionSchema — label ────────────────────────────────────────────

def test_label_valid():
    f = FieldInstructionSchema(key="name", label="Full Name")
    assert f.label == "Full Name"


def test_label_rejects_control_chars():
    with pytest.raises(ValidationError, match="control characters"):
        FieldInstructionSchema(key="name", label="Label\x00inject")


def test_label_rejects_newline():
    with pytest.raises(ValidationError, match="control characters"):
        FieldInstructionSchema(key="name", label="Line1\nLine2")


def test_label_rejects_too_long():
    with pytest.raises(ValidationError, match="200 characters"):
        FieldInstructionSchema(key="name", label="x" * 201)


# ── FieldInstructionSchema — description ─────────────────────────────────────

def test_description_rejects_control_chars():
    with pytest.raises(ValidationError, match="control characters"):
        FieldInstructionSchema(key="name", label="Name", description="desc\x1finject")


def test_description_rejects_too_long():
    with pytest.raises(ValidationError, match="500 characters"):
        FieldInstructionSchema(key="name", label="Name", description="x" * 501)


# ── FieldInstructionSchema — min_confidence ──────────────────────────────────

def test_min_confidence_valid():
    f = FieldInstructionSchema(key="name", label="Name", min_confidence=0.8)
    assert f.min_confidence == 0.8


def test_min_confidence_rejects_above_1():
    with pytest.raises(ValidationError, match="between 0.0 and 1.0"):
        FieldInstructionSchema(key="name", label="Name", min_confidence=1.1)


def test_min_confidence_rejects_negative():
    with pytest.raises(ValidationError, match="between 0.0 and 1.0"):
        FieldInstructionSchema(key="name", label="Name", min_confidence=-0.1)


def test_min_confidence_none_is_allowed():
    f = FieldInstructionSchema(key="name", label="Name", min_confidence=None)
    assert f.min_confidence is None


# ── ProcessRequest — pdf_url ──────────────────────────────────────────────────

def test_pdf_url_https_valid():
    r = ProcessRequest(pdf_url="https://example.com/doc.pdf")
    assert r.pdf_url == "https://example.com/doc.pdf"


def test_pdf_url_http_valid():
    r = ProcessRequest(pdf_url="http://example.com/doc.pdf")
    assert r.pdf_url == "http://example.com/doc.pdf"


def test_pdf_url_rejects_data_uri():
    with pytest.raises(ValidationError, match="http or https"):
        ProcessRequest(pdf_url="data:application/pdf;base64,abc")


def test_pdf_url_rejects_ftp():
    with pytest.raises(ValidationError, match="http or https"):
        ProcessRequest(pdf_url="ftp://example.com/doc.pdf")


# ── ProcessRequest — field_instructions limit ─────────────────────────────────

def test_field_instructions_limit_enforced():
    fields = [{"key": f"f{i}", "label": f"Field {i}"} for i in range(51)]
    with pytest.raises(ValidationError, match="50"):
        ProcessRequest(pdf_url="https://example.com/doc.pdf", field_instructions=fields)


def test_field_instructions_at_limit_passes():
    fields = [{"key": f"f{i:02d}", "label": f"Field {i}"} for i in range(50)]
    r = ProcessRequest(pdf_url="https://example.com/doc.pdf", field_instructions=fields)
    assert len(r.field_instructions) == 50
