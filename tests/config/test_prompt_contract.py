from pathlib import Path


def test_integrated_context_prompts_treat_metadata_as_non_literal_hints() -> None:
    for path in (Path("prompts/gemini.md"), Path("prompts/qwen.md")):
        text = path.read_text(encoding="utf-8")
        assert "non-literal" in text
        assert "speaker labels" in text
        assert "relative-age markers" in text
        assert "must not copy" in text
