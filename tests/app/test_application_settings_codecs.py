from __future__ import annotations

import itertools

import pytest

from puripuly_heart.app.ports.application_settings import (
    CaptureTargetValue,
    ClearSecretCommand,
    DesktopOverlayValue,
    GithubStarClickedCommand,
    GithubStarEligibleLaunchCountCommand,
    GithubStarLastShownAtCommand,
    GithubStarShowCountCommand,
    GithubStarTranslationSuccessObservedCommand,
    IntegratedContextBootstrappedCommand,
    LocalExtraBodyValue,
    OperationalField,
    OperationalStateSnapshot,
    OverlayCalibrationValue,
    OverlayOscOutputSettingsCommand,
    PeerTranslationEulaAcceptedCommand,
    SecretMetadata,
    SecretSourceStatus,
    SecretVerificationStatus,
    SetSecretCommand,
    SettingChange,
    SettingsField,
    StringListMapValue,
    StringMapValue,
    SttLanguageAudioSettingsCommand,
    TranslationFallbackValue,
    TranslationProviderSettingsCommand,
    UiPromptClipboardSettingsCommand,
)
from puripuly_heart.app.services.application_settings_codecs import (
    FIELD_CODECS,
    OPENROUTER_SELECTION_ALIASES,
    OPERATIONAL_CODECS,
    CodecKind,
    ProviderDiscriminatorState,
    ProviderSelectionCommand,
    convert_provider_selection,
    project_provider_selection,
)
from puripuly_heart.config.settings_vnext.schema import UserIntentSettings


def _valid_value(field: SettingsField):  # noqa: ANN202
    codec = FIELD_CODECS[field]
    if codec.choices:
        return sorted(codec.choices)[0]
    return {
        CodecKind.TEXT: " value ",
        CodecKind.EMPTY_TEXT: "",
        CodecKind.OPTIONAL_TEXT: " value ",
        CodecKind.BOOLEAN: True,
        CodecKind.INTEGER: int(codec.minimum or 1),
        CodecKind.NUMBER: float(codec.minimum or 0.5),
        CodecKind.STRING_LIST: ("en", "ko", "en"),
        CodecKind.STRING_MAP: StringMapValue((("gemma4", "managed"),)),
        CodecKind.STRING_LIST_MAP: StringListMapValue((("en", ("airi",)),)),
        CodecKind.LOCAL_EXTRA_BODY: LocalExtraBodyValue(()),
        CodecKind.FALLBACK: TranslationFallbackValue(
            True, "gemma4", "openrouter", "openrouter_gemma4_26b_a4b"
        ),
        CodecKind.CALIBRATION: OverlayCalibrationValue(),
        CodecKind.DESKTOP_OVERLAY: DesktopOverlayValue(),
        CodecKind.CAPTURE_TARGET: CaptureTargetValue(),
    }[codec.kind]


@pytest.mark.parametrize("field", tuple(SettingsField))
def test_every_settings_field_has_one_explicit_owner_path_codec_and_round_trip(field) -> None:
    assert set(FIELD_CODECS) == set(SettingsField)
    codec = FIELD_CODECS[field]
    assert codec.field is field
    assert codec.owner.value
    assert codec.canonical_paths
    assert all(path and all(part for part in path) for path in codec.canonical_paths)
    encoded = codec.encode(_valid_value(field))
    assert tuple(path for path, _ in encoded) == codec.canonical_paths
    decoded = codec.decode(tuple(value for _, value in encoded))
    assert codec.encode(decoded) == encoded


@pytest.mark.parametrize("field", tuple(SettingsField))
def test_every_settings_field_rejects_invalid_type_and_value(field) -> None:
    codec = FIELD_CODECS[field]
    invalid_type = (
        1
        if codec.kind in {CodecKind.TEXT, CodecKind.EMPTY_TEXT, CodecKind.OPTIONAL_TEXT}
        else "invalid"
    )
    with pytest.raises((TypeError, ValueError)):
        codec.encode(invalid_type)
    if codec.choices:
        with pytest.raises(ValueError):
            codec.encode("not-a-choice")
    if codec.minimum is not None:
        with pytest.raises(ValueError):
            codec.encode(
                int(codec.minimum - 1) if codec.kind == CodecKind.INTEGER else codec.minimum - 1
            )


def test_composite_fallback_calibration_and_desktop_overlay_are_leaf_codecs() -> None:
    assert len(FIELD_CODECS[SettingsField.TRANSLATION_FALLBACK].canonical_paths) == 4
    assert len(FIELD_CODECS[SettingsField.OVERLAY_CALIBRATION].canonical_paths) == len(
        OverlayCalibrationValue.__dataclass_fields__
    )


def _at_path(root, path):  # noqa: ANN001, ANN202
    value = root
    for part in path:
        value = getattr(value, part)
    return value


def _canonical_default_value(field: SettingsField):  # noqa: ANN202
    codec = FIELD_CODECS[field]
    intent = UserIntentSettings()
    if codec.kind == CodecKind.FALLBACK:
        value = intent.translation.fallback
        return TranslationFallbackValue(
            value.enabled, value.model, value.connection, value.selection_alias
        )
    if codec.kind == CodecKind.CALIBRATION:
        value = intent.overlay.calibration
        return OverlayCalibrationValue(
            value.anchor,
            value.offset_x,
            value.offset_y,
            value.distance,
            value.text_scale,
            value.background_alpha,
        )
    if codec.kind == CodecKind.DESKTOP_OVERLAY:
        value = intent.overlay.desktop_flet
        return DesktopOverlayValue(
            value.size_preset,
            value.position.x,
            value.position.y,
            value.visual.background_alpha,
        )
    if codec.kind == CodecKind.CAPTURE_TARGET:
        return CaptureTargetValue()
    value = _at_path(intent, codec.canonical_paths[0])
    if codec.kind == CodecKind.STRING_MAP:
        return StringMapValue(tuple(value.items()))
    if codec.kind == CodecKind.STRING_LIST_MAP:
        return StringListMapValue(tuple((key, tuple(items)) for key, items in value.items()))
    if codec.kind == CodecKind.LOCAL_EXTRA_BODY:
        from puripuly_heart.app.ports.application_settings import JsonScalarEntry

        return LocalExtraBodyValue(tuple(JsonScalarEntry(key, item) for key, item in value.items()))
    if codec.kind == CodecKind.STRING_LIST:
        return tuple(value)
    return value


def test_all_79_paths_are_unique_and_canonical_defaults_round_trip() -> None:
    paths = [path for codec in FIELD_CODECS.values() for path in codec.canonical_paths]
    assert len(paths) == 80
    assert len(set(paths)) == 80
    for field, codec in FIELD_CODECS.items():
        encoded = codec.encode(_canonical_default_value(field))
        assert codec.encode(codec.decode(tuple(value for _, value in encoded))) == encoded


def test_codec_domains_match_runtime_source_enums_and_strict_positive_validators() -> None:
    from puripuly_heart.config.runtime_resolution import (
        STT_PROVIDERS,
        TRANSLATION_CONNECTIONS,
    )
    from puripuly_heart.config.settings import (
        CerebrasLLMModel,
        OpenRouterLLMModel,
        QwenLLMModel,
        TranslationModel,
    )

    assert FIELD_CODECS[SettingsField.PROVIDER_STT].choices == frozenset(STT_PROVIDERS)
    assert FIELD_CODECS[SettingsField.PROVIDER_PEER_STT].choices == frozenset(STT_PROVIDERS)
    assert FIELD_CODECS[SettingsField.LOCAL_LLM_BACKEND].choices == frozenset({"ollama"})
    assert FIELD_CODECS[SettingsField.OPENROUTER_ROUTING_MODE].choices == frozenset({"latency"})
    assert FIELD_CODECS[SettingsField.OPENROUTER_PROVIDER_ROUTING].choices == frozenset(
        {"default", "deepseek_only", "google_gemini_latency"}
    )
    assert FIELD_CODECS[SettingsField.TRANSLATION_MODEL].choices == frozenset(
        member.value for member in TranslationModel
    )
    assert FIELD_CODECS[SettingsField.TRANSLATION_CONNECTION].choices == frozenset(
        TRANSLATION_CONNECTIONS
    )
    assert FIELD_CODECS[SettingsField.OPENROUTER_LLM_MODEL].choices == frozenset(
        member.value for member in OpenRouterLLMModel
    )
    assert FIELD_CODECS[SettingsField.QWEN_LLM_MODEL].choices == frozenset(
        member.value for member in QwenLLMModel
    )
    assert FIELD_CODECS[SettingsField.CEREBRAS_LLM_MODEL].choices == frozenset(
        member.value for member in CerebrasLLMModel
    )
    for field in (SettingsField.STT_DRAIN_TIMEOUT_S, SettingsField.SONIOX_STT_KEEPALIVE_S):
        with pytest.raises(ValueError):
            FIELD_CODECS[field].encode(0.0)
        assert FIELD_CODECS[field].encode(0.001)[0][1] == 0.001
    assert FIELD_CODECS[SettingsField.LLM_CONCURRENCY_LIMIT].encode(65)[0][1] == 65
    assert FIELD_CODECS[SettingsField.AUDIO_RING_BUFFER_MS].encode(60001)[0][1] == 60001
    assert FIELD_CODECS[SettingsField.STT_DRAIN_TIMEOUT_S].encode(61)[0][1] == 61.0
    assert FIELD_CODECS[SettingsField.SONIOX_STT_KEEPALIVE_S].encode(61)[0][1] == 61.0


@pytest.mark.parametrize(
    "field,value",
    (
        (SettingsField.PROVIDER_STT, "unknown"),
        (SettingsField.PROVIDER_PEER_STT, "unknown"),
        (SettingsField.OPENROUTER_ROUTING_MODE, "price"),
        (SettingsField.OPENROUTER_PROVIDER_ROUTING, "deepinfra"),
        (SettingsField.PEER_SOURCE_MODE, "automatic"),
        (SettingsField.DESKTOP_VAD_THRESHOLD, 1.01),
        (SettingsField.STT_VAD_THRESHOLD, float("nan")),
        (SettingsField.LOCAL_LLM_EXTRA_BODY, LocalExtraBodyValue(())),
        (
            SettingsField.STT_CUSTOM_TERMS,
            StringListMapValue((("en", ("",)),)),
        ),
    ),
)
def test_corruption_matrix_rejects_invalid_nested_or_domain_values(field, value) -> None:
    if field == SettingsField.LOCAL_LLM_EXTRA_BODY:
        from puripuly_heart.app.ports.application_settings import JsonScalarEntry

        value = LocalExtraBodyValue((JsonScalarEntry("api_key", "secret"),))
    with pytest.raises((TypeError, ValueError)):
        FIELD_CODECS[field].encode(value)
    assert FIELD_CODECS[SettingsField.OVERLAY_DESKTOP_FLET].canonical_paths == (
        ("overlay", "desktop_flet", "size_preset"),
        ("overlay", "desktop_flet", "position", "x"),
        ("overlay", "desktop_flet", "position", "y"),
        ("overlay", "desktop_flet", "visual", "background_alpha"),
    )


def test_composite_wrappers_validate_at_construction_and_are_frozen() -> None:
    from dataclasses import FrozenInstanceError

    invalid_factories = (
        lambda: TranslationFallbackValue(False, "gemma4", "managed", "none"),
        lambda: OverlayCalibrationValue(distance=0),
        lambda: OverlayCalibrationValue(background_alpha=2),
        lambda: DesktopOverlayValue(size_preset="huge"),
        lambda: DesktopOverlayValue(x=float("inf")),
        lambda: DesktopOverlayValue(background_alpha=-1),
    )
    for factory in invalid_factories:
        with pytest.raises((TypeError, ValueError)):
            factory()
    values = (
        TranslationFallbackValue(False, "deepseek_v4_flash", "official_byok", "none"),
        OverlayCalibrationValue(),
        DesktopOverlayValue(),
    )
    for value in values:
        with pytest.raises(FrozenInstanceError):
            value.__setattr__(next(iter(value.__dataclass_fields__)), "mutated")


PROVIDER_MATRIX = (
    ("gemini", "gemini-3-flash-preview", "byok", "gemini3_flash", "official_byok"),
    ("gemini", "gemini-3.1-flash-lite", "byok", "gemini31_flash_lite", "official_byok"),
    ("deepseek", "deepseek-v4-flash", "byok", "deepseek_v4_flash", "official_byok"),
    ("deepseek", "deepseek-v4-pro", "byok", "deepseek_v4_pro", "official_byok"),
    ("qwen", "qwen3.5-plus", "byok", "qwen35_plus", "official_byok"),
    ("qwen", "qwen3.5-flash", "byok", "deepseek_v4_flash", "managed"),
    ("cerebras", "gemma-4-31b", "byok", "gemma4_31b_cerebras", "official_byok"),
    ("local_llm", "llama3.1:8b", "none", "local_llm", "ollama"),
    ("openrouter", "google/gemma-4-26b-a4b-it", "managed", "gemma4", "managed"),
    ("openrouter", "google/gemma-4-26b-a4b-it", "byok", "gemma4", "openrouter"),
    ("openrouter", "deepseek/deepseek-v4-flash", "byok", "deepseek_v4_flash", "openrouter"),
    ("openrouter", "qwen/qwen3.5-flash-02-23", "byok", "deepseek_v4_flash", "managed"),
)


@pytest.mark.parametrize("provider,model,source,canonical_model,connection", PROVIDER_MATRIX)
def test_complete_provider_selection_matrix(
    provider, model, source, canonical_model, connection
) -> None:
    result = convert_provider_selection(ProviderSelectionCommand(provider, model, source))
    assert (result.model, result.connection) == (canonical_model, connection)
    assert dict(result.connection_history.entries)[canonical_model] == connection
    assert result.openrouter_routing_mode == "latency"
    assert result.openrouter_provider_routing == "default"


def test_all_supported_openrouter_profiles_project_round_trip() -> None:
    from puripuly_heart.config.llm_profiles import PROFILE_BY_ALIAS

    for alias, profile in PROFILE_BY_ALIAS.items():
        result = convert_provider_selection(
            ProviderSelectionCommand(
                "openrouter",
                profile.openrouter_model,
                profile.openrouter_source,
                selection_alias=alias,
            )
        )
        assert result.openrouter_model == profile.openrouter_model
        assert result.openrouter_selected_source == profile.openrouter_source
        assert result.openrouter_selection_alias == alias


def test_openrouter_selection_alias_codec_and_surface_use_one_profile_domain() -> None:
    from puripuly_heart.config.llm_profiles import PROFILE_BY_ALIAS

    codec = FIELD_CODECS[SettingsField.OPENROUTER_SELECTION_ALIAS]
    assert OPENROUTER_SELECTION_ALIASES == frozenset(PROFILE_BY_ALIAS)
    assert codec.choices == OPENROUTER_SELECTION_ALIASES
    for alias in PROFILE_BY_ALIAS:
        change = SettingChange(SettingsField.OPENROUTER_SELECTION_ALIAS, alias)
        assert TranslationProviderSettingsCommand((change,), "r").changes == (change,)
        assert codec.encode(alias)[0][1] == alias
        with pytest.raises(ValueError):
            SttLanguageAudioSettingsCommand((change,), "r")
        with pytest.raises(ValueError):
            OverlayOscOutputSettingsCommand((change,), "r")
        with pytest.raises(ValueError):
            UiPromptClipboardSettingsCommand((change,), "r")
    assert codec.encode(None)[0][1] is None
    assert codec.encode("")[0][1] is None
    for invalid in ("arbitrary", "openrouter:byok:google/gemma-4-26b-a4b-it", "none"):
        with pytest.raises(ValueError):
            codec.encode(invalid)
        with pytest.raises(ValueError):
            convert_provider_selection(
                ProviderSelectionCommand(
                    "openrouter",
                    "google/gemma-4-26b-a4b-it",
                    "byok",
                    selection_alias=invalid,
                )
            )


@pytest.mark.parametrize(
    "model,source,routing",
    (
        ("deepseek/deepseek-v4-flash", "byok", "deepseek_only"),
        ("google/gemini-3-flash-preview", "byok", "google_gemini_latency"),
        ("google/gemini-3.1-flash-lite", "byok", "google_gemini_latency"),
    ),
)
def test_supported_openrouter_provider_routing_domains(model, source, routing) -> None:
    result = convert_provider_selection(
        ProviderSelectionCommand("openrouter", model, source, provider_routing=routing)
    )
    assert result.openrouter_provider_routing == routing


def test_provider_selection_rejects_ambiguity_inconsistency_and_is_order_independent() -> None:
    with pytest.raises(ValueError):
        convert_provider_selection(ProviderSelectionCommand("openrouter", "", "managed"))
    with pytest.raises(ValueError):
        convert_provider_selection(
            ProviderSelectionCommand(
                "openrouter",
                "google/gemma-4-26b-a4b-it",
                "managed",
                selection_alias="gemma4_byok",
            )
        )
    entries = (("qwen35_plus", "official_byok"), ("gemma4", "managed"))
    results = {
        convert_provider_selection(
            ProviderSelectionCommand(
                "deepseek",
                "deepseek-v4-pro",
                "byok",
                connection_history=StringMapValue(order),
            )
        )
        for order in itertools.permutations(entries)
    }
    assert len(results) == 1


def test_provider_discriminator_state_rejects_corrupt_domains_and_profiles() -> None:
    invalid_factories = (
        lambda: ProviderDiscriminatorState(qwen_model="unknown"),
        lambda: ProviderDiscriminatorState(local_backend="openai_compatible"),
        lambda: ProviderDiscriminatorState(openrouter_routing_mode="price"),
        lambda: ProviderDiscriminatorState(
            openrouter_selected_source="managed", openrouter_selection_alias=None
        ),
        lambda: ProviderDiscriminatorState(
            openrouter_model="google/gemma-4-26b-a4b-it",
            openrouter_selected_source="managed",
            openrouter_selection_alias="gemma4_byok",
        ),
        lambda: ProviderDiscriminatorState(
            openrouter_selected_source="none", openrouter_selection_alias="gemma4_managed"
        ),
    )
    for factory in invalid_factories:
        with pytest.raises((TypeError, ValueError)):
            factory()


@pytest.mark.parametrize(
    "history,expected",
    (
        (("deepseek_v4_flash", "official_byok"), "official_byok"),
        (("deepseek_v4_flash", "openrouter"), "openrouter"),
        (("deepseek_v4_flash", "managed_china"), "managed_china"),
        (("deepseek_v4_flash", "invalid"), "managed"),
        (("unknown", "official_byok"), "managed"),
    ),
)
def test_qwen_flash_uses_valid_deepseek_history_or_existing_default(history, expected) -> None:
    for provider, model, source in (
        ("qwen", "qwen3.5-flash", "byok"),
        ("openrouter", "qwen/qwen3.5-flash-02-23", "byok"),
    ):
        result = convert_provider_selection(
            ProviderSelectionCommand(
                provider,
                model,
                source,
                connection_history=StringMapValue((history,)),
            )
        )
        assert result.model == "deepseek_v4_flash"
        assert result.connection == expected
        assert dict(result.connection_history.entries)["deepseek_v4_flash"] == expected
        assert convert_provider_selection(project_provider_selection(result)) == result


def test_stateful_transition_matrix_preserves_unrelated_discriminators_and_history() -> None:
    from puripuly_heart.config.llm_profiles import PROFILE_BY_ALIAS

    retained_states = [ProviderDiscriminatorState()]
    retained_states.append(
        ProviderDiscriminatorState(
            openrouter_selected_source="none", openrouter_selection_alias=None
        )
    )
    retained_states.extend(
        ProviderDiscriminatorState(
            openrouter_model=profile.openrouter_model,
            openrouter_selected_source=profile.openrouter_source,
            openrouter_selection_alias=alias,
        )
        for alias, profile in PROFILE_BY_ALIAS.items()
    )
    selections = [
        (provider, model, source, None) for provider, model, source, _, _ in PROVIDER_MATRIX
    ]
    selections.extend(
        ("openrouter", profile.openrouter_model, profile.openrouter_source, alias)
        for alias, profile in PROFILE_BY_ALIAS.items()
    )
    for retained in retained_states:
        for provider, model, source, alias in selections:
            result = convert_provider_selection(
                ProviderSelectionCommand(
                    provider,
                    model,
                    source,
                    selection_alias=alias,
                    connection_history=StringMapValue((("deepseek_v4_flash", "official_byok"),)),
                    retained=retained,
                )
            )
            if provider == "openrouter":
                assert result.openrouter_model == model
                assert result.openrouter_selected_source == source
            else:
                assert result.openrouter_model == retained.openrouter_model
                assert result.openrouter_selected_source == retained.openrouter_selected_source
                assert result.openrouter_selection_alias == retained.openrouter_selection_alias
            assert dict(result.connection_history.entries)[result.model] == result.connection
            assert result.qwen_model == (model if provider == "qwen" else retained.qwen_model)
            assert result.cerebras_model == (
                model if provider == "cerebras" else retained.cerebras_model
            )
            assert result.gemini_model == (model if provider == "gemini" else retained.gemini_model)
            assert result.deepseek_model == (
                model if provider == "deepseek" else retained.deepseek_model
            )
            assert result.local_model == (
                model if provider == "local_llm" else retained.local_model
            )
            assert convert_provider_selection(project_provider_selection(result)) == result


def test_secret_contracts_redact_value_and_protocol_shapes_conform() -> None:
    command = SetSecretCommand("openrouter_api_key", "sensitive")
    assert "sensitive" not in repr(command)
    assert command == SetSecretCommand("openrouter_api_key", "other")
    assert ClearSecretCommand("openrouter_api_key").key == command.key
    metadata = SecretMetadata(
        command.key,
        True,
        "revision",
        SecretVerificationStatus.VERIFIED,
        SecretSourceStatus.KEYRING,
    )
    assert not hasattr(metadata, "masked")


def test_setting_payload_construction_rejects_nested_mutability() -> None:
    from dataclasses import FrozenInstanceError

    with pytest.raises(TypeError):
        SettingChange(SettingsField.PEER_EXPECTED_LANGUAGES, (["en"],))
    source = [("gemma4", "managed")]
    frozen = StringMapValue(source)
    source.append(("gemini3_flash", "official_byok"))
    assert frozen.entries == (("gemma4", "managed"),)
    with pytest.raises(TypeError):
        StringMapValue((("gemma4", 1),))
    from puripuly_heart.app.ports.application_settings import JsonScalarEntry

    with pytest.raises(TypeError):
        JsonScalarEntry("nested", [])
    with pytest.raises(TypeError):
        LocalExtraBodyValue(({"key": "value"},))
    term_entries = [("en", ("airi",))]
    frozen_terms = StringListMapValue(term_entries)
    term_entries.append(("ko", ("아이리",)))
    assert frozen_terms.entries == (("en", ("airi",)),)
    scalar_entries = [JsonScalarEntry("reasoning_effort", "none")]
    frozen_extra = LocalExtraBodyValue(scalar_entries)
    scalar_entries.clear()
    assert frozen_extra.entries == (JsonScalarEntry("reasoning_effort", "none"),)
    with pytest.raises(FrozenInstanceError):
        frozen_extra.entries[0].value = "high"
    mutable_leaves = [(("github_star_prompt", "clicked"), True)]
    snapshot = OperationalStateSnapshot(mutable_leaves, "r")
    mutable_leaves.append((("github_star_prompt", "show_count"), 1))
    assert len(snapshot.leaves) == 1
    with pytest.raises(TypeError):
        OperationalStateSnapshot(((("bad",), []),), "r")


def test_each_command_surface_rejects_every_cross_surface_field() -> None:
    command_by_surface = {
        "translation_provider": TranslationProviderSettingsCommand,
        "stt_language_audio": SttLanguageAudioSettingsCommand,
        "overlay_osc_output": OverlayOscOutputSettingsCommand,
        "ui_prompt_clipboard": UiPromptClipboardSettingsCommand,
    }
    for field, codec in FIELD_CODECS.items():
        for surface, command_type in command_by_surface.items():
            change = SettingChange(field, _canonical_default_value(field))
            if surface == codec.owner.value:
                assert command_type((change,), "r").changes == (change,)
            else:
                with pytest.raises(ValueError):
                    command_type((change,), "r")


OPERATIONAL_CASES = (
    (OperationalField.GITHUB_STAR_CLICKED, GithubStarClickedCommand(True, "r"), True),
    (
        OperationalField.GITHUB_STAR_LAST_SHOWN_AT,
        GithubStarLastShownAtCommand(None, "r"),
        None,
    ),
    (OperationalField.GITHUB_STAR_SHOW_COUNT, GithubStarShowCountCommand(1, "r"), 1),
    (
        OperationalField.GITHUB_STAR_TRANSLATION_SUCCESS_OBSERVED,
        GithubStarTranslationSuccessObservedCommand(True, "r"),
        True,
    ),
    (
        OperationalField.GITHUB_STAR_ELIGIBLE_LAUNCH_COUNT,
        GithubStarEligibleLaunchCountCommand(1, "r"),
        1,
    ),
    (
        OperationalField.PEER_TRANSLATION_EULA_ACCEPTED,
        PeerTranslationEulaAcceptedCommand(True, "r"),
        True,
    ),
    (
        OperationalField.INTEGRATED_CONTEXT_BOOTSTRAPPED,
        IntegratedContextBootstrappedCommand(True, "r"),
        True,
    ),
)


@pytest.mark.parametrize("field,command,expected", OPERATIONAL_CASES)
def test_each_operational_field_has_specific_typed_codec(field, command, expected) -> None:
    assert set(OPERATIONAL_CODECS) == set(OperationalField)
    path, value = OPERATIONAL_CODECS[field].encode(command)
    assert path == tuple(field.value.split("."))
    assert value == expected


@pytest.mark.parametrize("field,command,_", OPERATIONAL_CASES)
def test_operational_codecs_reject_wrong_command_and_corrupt_values(field, command, _) -> None:
    wrong = GithubStarShowCountCommand(-1, "r")
    with pytest.raises((TypeError, ValueError)):
        OPERATIONAL_CODECS[field].encode(wrong)
    if isinstance(command, GithubStarShowCountCommand):
        with pytest.raises(ValueError):
            OPERATIONAL_CODECS[field].encode(GithubStarShowCountCommand(-1, "r"))
