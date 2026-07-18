# PRD: vNext Provider-Neutral Peer Auto-Detection

Status: Approved for the bounded implementation described by the approved execution bundle.

Target baseline: `vnext`

## Goal

Let a user enable automatic peer-language detection with Soniox or Local Qwen GPU and translate detected speech through the existing LLM pipeline into the configured peer target language. Soniox preserves chronological mixed-language runs; Qwen GPU produces one language run for the whole utterance. Preserve the existing subtitle layout and preferences.

## vNext architecture target

This is a vNext-native feature, not a direct port of a legacy settings or Hub implementation.

- **Persisted user intent:** peer source mode (`manual` or `auto`) and optional expected participant languages belong to the canonical vNext language intent. Expected participant languages remain Soniox-only settings and do not replace the independently selected self source language.
- **Resolved runtime config:** automatic detection is a provider capability available only to peer Soniox and peer Local Qwen GPU. Self Soniox sessions derive a strict single-language restriction from the explicitly selected source language. Manual peer Soniox sessions derive a non-strict hint from the selected peer source language. Automatic peer Soniox sessions enable language identification and use expected participant languages as optional non-strict hints. Manual peer Local Qwen GPU requests carry a per-utterance Qwen language hint; automatic requests omit it. Unsupported provider/mode combinations resolve defensively to `manual` while preserving the saved manual peer source language.
- **Runtime-only data:** final token language, timestamps, language runs, parent/child identities, queue state, cancellation state, and latency bookkeeping are never persisted as user settings.
- **Provider boundary:** Soniox and Local Qwen GPU may enrich final STT results with provider-neutral final language-run data. Other STT providers retain their current text-only final-result behavior.
- **Lifecycle owner:** introduce a dedicated vNext **Peer Final-Runs Lifecycle Owner** in the orchestration boundary. A finalized peer Utterance Segment is its parent work item. The owner creates ordered child runs, serializes their LLM work, and closes the parent only after every child is terminal. `ClientHub` composes the owner and drives its startup/shutdown, but does not directly own the parent/child queue or child work. No unmanaged background task may own this work.
- **Output routing:** the Peer Final-Runs Lifecycle Owner publishes every child as peer-channel output through the existing output boundary. `OutputRuntime` remains the transport/ingress-close owner; it does not own peer translation work. Existing subtitle/dashboard routing and the VRChat chatbox hard-deny remain enforced.

If vNext retains a legacy settings compatibility adapter, it must accept old settings with safe defaults and migrate them forward. The vNext intent model remains canonical; the feature must not make legacy mutable settings the runtime source of truth.

## Non-goals

- Add speaker labels or language tags to subtitles, or change the optional peer-original-text preference.
- Identify a speaker, nationality, or distinguish Taiwan Mandarin from other Mandarin variants inside Soniox `zh`.
- Use Soniox native translation output.
- Regroup non-contiguous passages that share a language.
- Change the peer-chatbox hard-deny policy.
- Add self automatic detection, Qwen Cloud automatic detection, or language detection to Local CPU Auto.

## Resolved decisions

### User-visible behavior

- Add provider-neutral `Automatic detection` to the dashboard peer source-language selector as its first option, above the alphabetically listed languages including Arabic. Enable it only for Soniox and Local Qwen GPU.
- Keep the subtitle visually unchanged. Translation remains primary and the existing optional original peer text remains optional.

### Expected participant languages

- When peer STT is Soniox, show a **Peer automatic-detection languages** card in the Soniox/API Settings area.
- This is peer-only data. It never changes the strict language restriction derived from the self microphone source language.
- The list is optional. A non-empty selection becomes non-strict Soniox `language_hints`; an empty selection permits Soniox automatic detection without hints.
- The expected-language editor remains visible only when peer STT is Soniox and is never reused as a Qwen hint source.
- Switching between Soniox and Local Qwen GPU preserves automatic mode. Switching to an unsupported provider changes the mode to manual without replacing the saved manual peer source language.

### Recognition, translation, and segmentation

- Soniox is used only for STT and language identification. Existing LLM translation remains the translation path.
- Automatic peer sessions set `enable_language_identification: true`.
- Self sessions set one `language_hints` entry from the selected source language and set `language_hints_strict: true`.
- Manual peer sessions use the selected source language as a non-strict hint. Automatic peer sessions also keep expected-language hints non-strict and omit strict restriction.
- Manual peer Local Qwen GPU requests normalize the selected source language to a supported Qwen hint. Automatic peer requests omit the hint. The hint belongs to queued work, not shared model activation state.
- After local VAD ends speech, retain Soniox final tokens until `<fin>`, then group only adjacent final tokens sharing the same language in original time order.
- In Local Qwen GPU automatic mode, a valid detected language creates one `FinalLanguageRun` for the cleaned whole transcript. A missing detected language creates no run and falls back to the saved manual peer source language. An unsupported non-empty code is preserved for the existing unmapped-language source-only path.
- One finalized peer VAD utterance becomes a parent final-runs event. The Peer Final-Runs Lifecycle Owner gives its children unique identities and a serial translation/output lifecycle; a parent closes only after every child reaches translated, existing source-only fallback, or cancellation.
- Soniox `zh` is normalized to generic **Chinese** for LLM prompt/context/request construction. It is not converted to a persisted `zh-CN` or `zh-TW` preference and must never fall back to English. Unmapped languages use the existing safe source-only/failure path.

## Compatibility and output invariants

- Preserve existing manual peer source-language behavior and existing settings files.
- Persist explicit peer `manual | auto` mode and optional expected languages through vNext intent, serialization, resolved-runtime signatures, settings mutation, and safe migration/backup behavior. v31 converts persisted `soniox_auto` once at the migration boundary and never emits it again. Any retained legacy adapter must roundtrip compatible values without becoming the canonical runtime owner.
- Final-runs metadata is provider-neutral; providers without automatic-language capability retain current text-only events.
- Every peer child remains subtitle/dashboard-only and is hard-denied from VRChat chatbox output on success, fallback, cancellation, and cleanup.
- All user-facing copy uses i18n keys in every locale bundle.
- Diagnostics never persist secrets, raw provider payloads, real user audio, or real user transcript text.

## Risks

- Soniox language tags are token-level rather than guaranteed linguistic sentence boundaries. Mitigation: use adjacent final-token runs and preserve order.
- Loopback audio mixes peers and overlapping speech. Mitigation: do not promise speaker separation; include limited overlap in the PoC.
- Finalization may be early or late. Mitigation: retain VAD/manual-finalize lifecycle initially and tune only from evidence.
- Child output could reorder or leak after cancellation. Mitigation: the Peer Final-Runs Lifecycle Owner owns the parent queue, serial execution, cancellation, explicit closure, cleanup, and stale-completion coverage; `ClientHub` shuts it down with the surrounding runtime.

## User-approved release-evidence policy change

The user approved deterministic simulation in place of the real four-person loopback requirement. The readiness record must label this as simulated evidence and retain only language sequence, segment count, latency, failures, and the simulated marker. It must not retain speech text, audio, raw provider payloads, credentials, or real transcript data.

This replacement is simulated evidence only. It is not real-device, real-loopback, or production validation, and it does not authorize a production-readiness or production-deployment claim.

## PoC release gate

1. Use a controlled Soniox harness to retain final token `text`, `language`, finality, and ordering only in test artifacts, then derive runs at `<fin>`.
2. Run twice each: Korean-only, Japanese-only, Chinese/Taiwan Mandarin-only, ja→zh, zh→ja→ko, and an intentional same-language pause.
3. Each controlled fixture must preserve expected language-run order, have no lost or duplicate final token, normalize generic `zh` as Chinese, produce ordered terminal child outputs, close the parent after the final child, and preserve optional peer-original behavior with no new labels.
4. Run a deterministic four-participant zh/ja/ko simulation for normal turns and limited overlap. Retain only diagnostic-safe structure: language sequence, segment count, latency, failures, and an explicit simulated marker. This is not real-device, real-loopback, or production validation.
5. Any lost/reordered run, unclosed parent, peer-chatbox publication, or unsafe evidence blocks release readiness.

## Implementation outline

1. Add the vNext canonical peer language intent, compatibility migration/backup, capability-aware peer STT runtime resolution, dashboard activation, and Soniox Settings configuration UI.
2. Preserve Soniox and Local Qwen GPU final-language metadata behind a provider-neutral final-result DTO while retaining text-only behavior for other providers.
3. Add an explicit detected-language normalization service/mapper for LLM prompt, context, and request construction; generic `zh` maps to generic Chinese.
4. Add the dedicated Peer Final-Runs Lifecycle Owner at the orchestration boundary. It owns ordered child results, cancellation/fallback/stale completion, and parent closure/cleanup; `ClientHub` composes and shuts it down, while `OutputRuntime` remains limited to output transport and ingress closure.
5. Preserve subtitle/original-text preference and peer output routing; update every locale bundle and vNext architecture/regression tests.
6. Execute targeted tests, full Python regression and lint, then the safe PoC gate.

## Approval

Implementation must not begin until this brief and its execution bundle are approved.
