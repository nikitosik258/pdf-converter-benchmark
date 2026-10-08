from pathlib import Path

from pdf_benchmark.benchmark.config import BenchmarkConfig


def test_benchmark_config_has_10_tools_and_5_documents():
    root = Path(__file__).resolve().parents[1]
    config = BenchmarkConfig.from_yaml(root / "config" / "benchmark.yaml")
    assert len(config.tools) == 10
    assert len(config.documents) == 5
    assert {d.document_id for d in config.documents} == {"D01", "D02", "D03", "D04", "D05"}
