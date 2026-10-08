\
from pathlib import Path
import yaml

from pdf_benchmark.benchmark.registry import TOOL_SPECS


def test_llamaparse_active_and_azure_preserved():
    root = Path(__file__).resolve().parents[1]
    cfg = yaml.safe_load((root / "config" / "benchmark.yaml").read_text(encoding="utf-8"))
    tools = cfg["benchmark"]["tools"]
    assert "llamaparse" in tools
    assert "azure_document_intelligence" not in tools
    assert "llamaparse" in TOOL_SPECS
    assert "azure_document_intelligence" in TOOL_SPECS
    assert (root / "src/pdf_benchmark/adapters/cloud/azure_document_intelligence_adapter.py").exists()
    assert (root / "config/tools/azure_document_intelligence.yaml").exists()
    assert (root / "tests/fixtures/cloud/azure.json").exists()
