from __future__ import annotations

from puripuly_heart.config.settings import MAX_CUSTOM_VOCAB_TERMS, AppSettings


def get_effective_custom_terms(settings: AppSettings, source_language: str) -> list[str]:
    if not settings.stt.custom_vocabulary_enabled:
        return []

    raw_terms = settings.stt.custom_terms.get(source_language, [])
    effective_terms: list[str] = []
    seen_terms: set[str] = set()
    for term in raw_terms:
        normalized_term = term.strip()
        if not normalized_term or normalized_term in seen_terms:
            continue
        if len(effective_terms) >= MAX_CUSTOM_VOCAB_TERMS:
            break
        seen_terms.add(normalized_term)
        effective_terms.append(normalized_term)
    return effective_terms
