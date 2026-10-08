import json
from pathlib import Path

import pytest

from pdf_benchmark.benchmark.cache import PairCache, cache_compatibility
from pdf_benchmark.benchmark.config import BenchmarkConfig
from pdf_benchmark.benchmark.registry import TOOL_SPECS
from pdf_benchmark.benchmark.runner import BenchmarkRunner, _copy_standardized_cache
from pdf_benchmark.benchmark.worker import run_adapter_worker
from pdf_benchmark.models import Page, RawToolResult, StandardizedDocument, ToolMetadata
from pdf_benchmark.utils.io import write_json


def dummy_runner(tmp_path):
    runner = object.__new__(BenchmarkRunner)
    runner.force_rerun = False
    runner.force_api = False
    runner.reuse_stale_raw = False
    return runner


def test_cloud_resume_reuses_standardized(tmp_path):
    runner = dummy_runner(tmp_path)
    cache = PairCache(tmp_path).ensure()
    cache.standardized.write_text("{}", encoding="utf-8")
    action, notes = runner._plan_adapter_action(
        spec=TOOL_SPECS["ocr_space"],
        raw_exists=False,
        standardized_exists=True,
        raw_compatible=True,
        standardized_compatible=True,
        incompatibility_reasons=[],
    )
    assert action == "reuse_standardized"


def test_cloud_raw_is_not_recalled(tmp_path):
    runner = dummy_runner(tmp_path)
    cache = PairCache(tmp_path).ensure()
    cache.raw_snapshot.write_text("{}", encoding="utf-8")
    action, notes = runner._plan_adapter_action(
        spec=TOOL_SPECS["mindee"],
        raw_exists=True,
        standardized_exists=False,
        raw_compatible=True,
        standardized_compatible=False,
        incompatibility_reasons=[],
    )
    assert action == "standardize_only"
    assert "cloud_api_not_recalled" in notes


def test_cloud_changed_config_refuses_silent_api_call(tmp_path):
    runner = dummy_runner(tmp_path)
    cache = PairCache(tmp_path).ensure()
    cache.raw_snapshot.write_text("{}", encoding="utf-8")
    with pytest.raises(RuntimeError, match="Refusing a silent API re-call"):
        runner._plan_adapter_action(
            spec=TOOL_SPECS["adobe_extract"],
            raw_exists=True,
            standardized_exists=False,
            raw_compatible=False,
            standardized_compatible=False,
            incompatibility_reasons=["tool_config_changed"],
        )


def test_force_api_is_explicit(tmp_path):
    runner = dummy_runner(tmp_path)
    runner.force_api = True
    cache = PairCache(tmp_path).ensure()
    cache.raw_snapshot.write_text("{}", encoding="utf-8")
    action, notes = runner._plan_adapter_action(
        spec=TOOL_SPECS["azure_document_intelligence"],
        raw_exists=True,
        standardized_exists=False,
        raw_compatible=True,
        standardized_compatible=False,
        incompatibility_reasons=[],
    )
    assert action == "run_adapter"
    assert "force_api=true" in notes


def _fingerprints(**changes):
    result = {
        "raw_acquisition": "raw-v1",
        "standardization": "standard-v1",
        "normalization": "normal-v1",
        "evaluation": "evaluation-v1",
    }
    result.update(changes)
    return result


def _manifest(cache, *, stages=None, source="pdf-v1", config="config-v1"):
    payload = {
        "source_sha256": source,
        "tool_config_sha256": config,
    }
    if stages is not None:
        payload["stage_fingerprints"] = stages
    cache.cache_manifest.write_text(json.dumps(payload), encoding="utf-8")


def test_stage_fingerprints_are_checked_independently(tmp_path):
    cache = PairCache(tmp_path).ensure()
    _manifest(cache, stages=_fingerprints())

    compatibility = cache_compatibility(
        cache,
        source_sha256="pdf-v1",
        tool_config_sha256="config-v1",
        stage_fingerprints=_fingerprints(standardization="standard-v2"),
    )

    assert compatibility.raw_compatible
    assert not compatibility.standardized_compatible
    assert not compatibility.normalized_compatible
    assert not compatibility.evaluation_compatible
    assert compatibility.reasons == ["standardization_fingerprint_changed"]


def test_legacy_manifest_reuses_raw_but_invalidates_standardized(tmp_path):
    cache = PairCache(tmp_path).ensure()
    _manifest(cache)

    compatibility = cache_compatibility(
        cache,
        source_sha256="pdf-v1",
        tool_config_sha256="config-v1",
        stage_fingerprints=_fingerprints(),
    )

    assert compatibility.raw_compatible
    assert not compatibility.standardized_compatible
    assert "standardization_fingerprint_missing" in compatibility.reasons


def test_cloud_standardizer_change_automatically_reuses_raw(tmp_path):
    runner = dummy_runner(tmp_path)
    cache = PairCache(tmp_path).ensure()
    cache.raw_snapshot.write_text("{}", encoding="utf-8")

    action, notes = runner._plan_adapter_action(
        spec=TOOL_SPECS["nutrient"],
        raw_exists=True,
        standardized_exists=False,
        raw_compatible=True,
        standardized_compatible=False,
        incompatibility_reasons=["standardization_fingerprint_changed"],
    )

    assert action == "standardize_only"
    assert notes == ["cloud_api_not_recalled", "standardization_cache_invalidated"]


def test_local_raw_change_does_not_restandardize_stale_raw(tmp_path):
    runner = dummy_runner(tmp_path)
    cache = PairCache(tmp_path).ensure()
    cache.raw_snapshot.write_text("{}", encoding="utf-8")

    action, _ = runner._plan_adapter_action(
        spec=TOOL_SPECS["pymupdf"],
        raw_exists=True,
        standardized_exists=False,
        raw_compatible=False,
        standardized_compatible=False,
        incompatibility_reasons=["source_sha256_changed"],
    )

    assert action == "run_adapter"


def test_normalization_change_keeps_standardized_reusable(tmp_path):
    cache = PairCache(tmp_path).ensure()
    cache.standardized.write_text("{}", encoding="utf-8")
    _manifest(cache, stages=_fingerprints())
    compatibility = cache_compatibility(
        cache,
        source_sha256="pdf-v1",
        tool_config_sha256="config-v1",
        stage_fingerprints=_fingerprints(normalization="normal-v2"),
    )

    assert compatibility.raw_compatible
    assert compatibility.standardized_compatible
    assert not compatibility.normalized_compatible

    runner = dummy_runner(tmp_path)
    action, _ = runner._plan_adapter_action(
        spec=TOOL_SPECS["pymupdf"],
        raw_exists=False,
        standardized_exists=True,
        raw_compatible=compatibility.raw_compatible,
        standardized_compatible=compatibility.standardized_compatible,
        incompatibility_reasons=compatibility.reasons,
    )
    assert action == "reuse_standardized"


def test_reuse_standardized_copies_local_assets(tmp_path):
    source = PairCache(tmp_path / "source").ensure()
    target = PairCache(tmp_path / "target").ensure()
    source.standardized.write_text('{"document_id":"D1"}', encoding="utf-8")
    asset = source.assets_dir / "nested" / "figure.png"
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"image-bytes")

    _copy_standardized_cache(source, target)

    assert target.standardized.read_bytes() == source.standardized.read_bytes()
    assert (target.assets_dir / "nested" / "figure.png").read_bytes() == b"image-bytes"


def test_standardize_only_reads_raw_from_old_cache_and_writes_new_stage(
    tmp_path,
    monkeypatch,
):
    raw_cache = PairCache(tmp_path / "legacy").ensure()
    target_cache = PairCache(tmp_path / "versions" / "new-stage").ensure()
    raw = RawToolResult(primary_artifact="raw/native.json", artifacts=["raw/native.json"])
    write_json(raw_cache.raw_snapshot, raw.model_dump(mode="json"))

    class FakeAdapter:
        def standardize(self, raw_result, raw_dir, assets_dir, *, document_id, pdf_path):
            assert raw_result == raw
            assert raw_dir == raw_cache.raw_dir
            assert assets_dir == target_cache.assets_dir
            return StandardizedDocument(
                document_id=document_id,
                source_pdf=str(pdf_path),
                tool=ToolMetadata(tool_name="fake", distribution_name="fake"),
                pages=[Page(page_number=1, width=100, height=100)],
            )

        def tool_metadata(self):
            return ToolMetadata(tool_name="fake", distribution_name="fake")

    monkeypatch.setattr(
        "pdf_benchmark.benchmark.worker.load_tool_config",
        lambda project_root, tool: {},
    )
    monkeypatch.setattr(
        "pdf_benchmark.benchmark.worker.create_adapter",
        lambda tool, config: FakeAdapter(),
    )
    pdf_path = tmp_path / "document.pdf"
    pdf_path.write_bytes(b"%PDF-test")

    result = run_adapter_worker(
        project_root=tmp_path,
        tool="pymupdf",
        pdf_path=pdf_path,
        document_id="D1",
        output_dir=target_cache.root,
        raw_cache_dir=raw_cache.root,
        seed=42,
        standardize_only=True,
    )

    assert result["status"] == "success"
    assert target_cache.standardized.exists()
    assert not raw_cache.standardized.exists()
    assert not target_cache.raw_snapshot.exists()


def test_standardize_only_rebases_moved_absolute_raw_paths(tmp_path, monkeypatch):
    raw_cache = PairCache(tmp_path / "legacy").ensure()
    target_cache = PairCache(tmp_path / "versions" / "new-stage").ensure()
    native = raw_cache.raw_dir / "saved" / "native.json"
    native.parent.mkdir(parents=True)
    native.write_text("{}", encoding="utf-8")
    stale_root = r"C:\Users\old\project\outputs\benchmark\cache\fake\D1\raw"
    raw = RawToolResult(
        primary_artifact=stale_root + r"\saved\native.json",
        artifacts=[stale_root + r"\saved\native.json"],
        metadata={"saved_dir": stale_root + r"\saved"},
    )
    write_json(raw_cache.raw_snapshot, raw.model_dump(mode="json"))

    class FakeAdapter:
        def standardize(self, raw_result, raw_dir, assets_dir, *, document_id, pdf_path):
            assert Path(raw_result.primary_artifact) == native.resolve()
            assert [Path(value) for value in raw_result.artifacts] == [native.resolve()]
            assert Path(raw_result.metadata["saved_dir"]) == native.parent.resolve()
            return StandardizedDocument(
                document_id=document_id,
                source_pdf=str(pdf_path),
                tool=ToolMetadata(tool_name="fake", distribution_name="fake"),
                pages=[Page(page_number=1, width=100, height=100)],
            )

        def tool_metadata(self):
            return ToolMetadata(tool_name="fake", distribution_name="fake")

    monkeypatch.setattr(
        "pdf_benchmark.benchmark.worker.load_tool_config",
        lambda project_root, tool: {},
    )
    monkeypatch.setattr(
        "pdf_benchmark.benchmark.worker.create_adapter",
        lambda tool, config: FakeAdapter(),
    )
    pdf_path = tmp_path / "document.pdf"
    pdf_path.write_bytes(b"%PDF-test")

    result = run_adapter_worker(
        project_root=tmp_path,
        tool="pymupdf",
        pdf_path=pdf_path,
        document_id="D1",
        output_dir=target_cache.root,
        raw_cache_dir=raw_cache.root,
        seed=42,
        standardize_only=True,
    )

    assert result["status"] == "success"
    standardized = StandardizedDocument.model_validate_json(
        target_cache.standardized.read_text(encoding="utf-8")
    )
    assert standardized.metadata["raw_snapshot_paths_rebased"] is True
    # The preserved acquisition snapshot remains byte-for-byte untouched.
    assert RawToolResult.model_validate_json(
        raw_cache.raw_snapshot.read_text(encoding="utf-8")
    ) == raw
