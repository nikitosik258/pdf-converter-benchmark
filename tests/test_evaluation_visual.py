import pytest

from pdf_benchmark.evaluation.diagram_metrics import calculate_diagram_metrics
from pdf_benchmark.evaluation.image_metrics import (
    calculate_image_metrics,
    detection_metrics,
)
from pdf_benchmark.models import BBox


BOX = BBox(x_min=0.1, y_min=0.2, x_max=0.7, y_max=0.8)


def test_detection_precision_recall_f1():
    m = detection_metrics(tp=2, fp=1, fn=2)
    assert m["detection_precision"] == pytest.approx(2 / 3)
    assert m["detection_recall"] == pytest.approx(1 / 2)
    assert m["detection_f1"] == pytest.approx(4 / 7)


def test_image_ideal():
    m = calculate_image_metrics(
        reference_bbox=BOX,
        prediction_bbox=BOX,
        reference_caption="Рис. 1. Прибор",
        prediction_caption="Рис. 1. Прибор",
        prediction_exists=True,
        extraction_success=True,
    )
    assert m["image_score"] == 1.0


def test_image_partial():
    other = BBox(x_min=0.2, y_min=0.2, x_max=0.7, y_max=0.8)
    m = calculate_image_metrics(
        reference_bbox=BOX,
        prediction_bbox=other,
        reference_caption="Рис. 1. Прибор",
        prediction_caption="Рис. 1. Устройство",
        prediction_exists=True,
        extraction_success=False,
    )
    assert 0.0 < m["image_score"] < 1.0


def test_image_missing():
    m = calculate_image_metrics(
        reference_bbox=BOX,
        prediction_bbox=None,
        reference_caption="Рис. 1. Прибор",
        prediction_caption=None,
        prediction_exists=False,
        extraction_success=False,
    )
    assert m["image_score"] == 0.0


def test_diagram_ideal():
    m = calculate_diagram_metrics(
        reference_bbox=BOX,
        prediction_bbox=BOX,
        reference_caption="Рис. 1. Архитектура",
        prediction_caption="Рис. 1. Архитектура",
        reference_text_elements=["Input", "Dense"],
        prediction_text_elements=["Input", "Dense"],
        reference_key_elements=["Input", "Dense"],
        prediction_key_elements=["Input", "Dense"],
        reference_subfigure_count=1,
        prediction_subfigure_count=1,
        prediction_exists=True,
    )
    assert m["diagram_score"] == 1.0


def test_diagram_missing():
    m = calculate_diagram_metrics(
        reference_bbox=BOX,
        prediction_bbox=None,
        reference_caption="Рис. 1",
        prediction_caption=None,
        reference_text_elements=["Input"],
        prediction_text_elements=[],
        reference_key_elements=["Input"],
        prediction_key_elements=[],
        reference_subfigure_count=1,
        prediction_subfigure_count=0,
        prediction_exists=False,
    )
    assert m["diagram_score"] == 0.0
