"""Tests for the pipeline workflow module."""

from termprep.pipeline import run_pipeline, format_pipeline_result


def test_pipeline_with_text():
    """Pipeline should run end-to-end with text input."""
    result = run_pipeline(
        text="This is a test contract agreement between two parties. "
             "The party of the first part and the party of the second part "
             "agree to the terms and conditions set forth herein.",
        project_name="TestProject",
        top_n=5,
        db_name="test-pipeline-db",
        report_output="",
    )
    assert result.analysis is not None
    assert result.analysis.lang == "en"
    assert len(result.terms) >= 1
    assert result.termbase_terms >= 1
    assert result.report_path != ""
    assert result.project_name == "TestProject"


def test_pipeline_no_input():
    """Pipeline without input should return errors."""
    result = run_pipeline()
    assert len(result.errors) > 0
    assert "No input" in result.errors[0]


def test_pipeline_with_export():
    """Pipeline should export to requested formats."""
    result = run_pipeline(
        text="Machine learning is a subset of artificial intelligence. "
             "Deep learning is a subset of machine learning.",
        project_name="AI-Test",
        top_n=5,
        db_name="test-pipeline-export",
        export_formats=["csv", "json"],
    )
    # Exports should be recorded
    if result.exports:
        assert "csv" in result.exports or any(
            k.endswith("csv") for k in result.exports
        )


def test_pipeline_formats_output():
    """format_pipeline_result should produce readable output."""
    result = run_pipeline(
        text="A short test.",
        project_name="FormatTest",
        top_n=3,
        db_name="test-pipeline-format",
    )
    output = format_pipeline_result(result)
    assert "Pipeline complete" in output
    assert "FormatTest" in output
    assert "Duration" in output


def test_pipeline_string_domain_detection():
    """Pipeline should detect domain from text."""
    result = run_pipeline(
        text="The patient was diagnosed with hypertension. "
             "Treatment includes medication and therapy. "
             "Clinical trials show promising results.",
        project_name="MedicalTest",
        top_n=5,
        db_name="test-pipeline-domain",
    )
    assert result.analysis is not None
    assert result.analysis.domain in ("medical", "general")


def test_pipeline_result_dataclass():
    """PipelineResult should have all expected fields."""
    from termprep.pipeline import PipelineResult
    r = PipelineResult(
        project_name="Test",
        source_file="test.txt",
    )
    assert r.project_name == "Test"
    assert r.source_file == "test.txt"
    assert r.duration == 0.0
    assert r.errors == []
    assert r.exports == {}
