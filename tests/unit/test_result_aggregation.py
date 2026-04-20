from src.models.result import ExtractedField, PageResult, aggregate_extracted_fields


def test_aggregate_extracted_fields_uses_longest_value_for_same_key_and_label():
    pages = [
        PageResult(
            page_number=1,
            extracted_fields=[
                ExtractedField(
                    key="ten_du_an",
                    label="Ten du an",
                    value="DA A",
                    confidence=0.95,
                )
            ],
        ),
        PageResult(
            page_number=2,
            extracted_fields=[
                ExtractedField(
                    key="ten_du_an",
                    label="Ten du an",
                    value="Du an ABC 2026",
                    confidence=0.80,
                )
            ],
        ),
    ]

    merged = aggregate_extracted_fields(pages)
    assert len(merged) == 1
    assert merged[0].key == "ten_du_an"
    assert merged[0].label == "Ten du an"
    assert merged[0].value == "Du an ABC 2026"


def test_aggregate_extracted_fields_keeps_distinct_label_or_key_separate():
    pages = [
        PageResult(
            page_number=1,
            extracted_fields=[
                ExtractedField(key="so_hop_dong", label="So hop dong", value="HD-01", confidence=0.9),
                ExtractedField(key="so_hop_dong", label="So HD", value="HD-01", confidence=0.8),
            ],
        ),
    ]

    merged = aggregate_extracted_fields(pages)
    assert len(merged) == 2
