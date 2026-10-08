from pdf_benchmark.evaluation import EvaluationFramework


def test_missing_required_table_object_is_exactly_zero():
    result = EvaluationFramework().evaluate_object({
        "tool": "x",
        "document_id": "D1",
        "page": 1,
        "object_id": "TAB",
        "object_type": "table",
        "reference": {
            "element_id": "gt",
            "page_number": 1,
            "rows": 1,
            "columns": 1,
            "cells": [{"row_index": 0, "column_index": 0, "text": "A"}],
        },
        "prediction": None,
    })
    assert result.object_score == 0.0
