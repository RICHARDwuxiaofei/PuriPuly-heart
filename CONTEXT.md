# PuriPuly

PuriPuly is a two-way conversation translator for VRChat. It turns spoken turns into natural localized text for the user's own speech and, when enabled, another participant's speech.

## Language

**Utterance**:
A single spoken turn that PuriPuly follows from speech detection through transcript, translation, and output.
_Avoid_: sentence, message, audio clip

**Transcript**:
Text recognized from speech before it is translated.
_Avoid_: transcription result, STT text

**Translation**:
Localized text produced from a transcript for the configured target language while preserving conversational tone.
_Avoid_: response, output text

**Channel**:
The side of a conversation that an utterance belongs to: either the user's own speech path or another participant's speech path when peer voice translation is enabled.
_Avoid_: stream, role, side, local user, remote user

**Context Memory**:
Recent conversation history that can be supplied to translation so nearby turns influence wording and tone.
_Avoid_: chat history, memory cache

**Translation Connection**:
The way PuriPuly obtains translation access for the selected translation model, such as managed access, a user's own provider account, or a local compatible endpoint.
_Avoid_: provider mode, API mode

**Managed Connection**:
A PuriPuly-managed translation connection that lets eligible users start through Discord authentication instead of bringing their own API key.
_Avoid_: free mode, trial key, hosted translation

**Broker**:
The PuriPuly-controlled authority for managed eligibility and credential issuance. The broker is not the translation provider and does not translate speech.
_Avoid_: translation proxy, OpenRouter proxy

**Talk Together Pass ID**:
A shareable pass identifier that appears after Discord verification and can be shared with a friend for extra managed usage together.
_Avoid_: referral code, invite code, pass code

**VR Subtitle Overlay**:
The in-VR display surface used to show transcripts and translations without relying only on the VRChat chatbox.
_Avoid_: overlay app, subtitle window

## Flagged ambiguities

- Use **Managed Connection** for the user-facing connection mode, **Managed Key** for the issued credential surface, and **Talk Together Pass ID** for the shareable invite identifier.
- Use **Broker** only for managed eligibility and credential issuance. Do not call it a translation proxy.

## Example dialogue

Developer: "Should a peer-channel utterance be sent to the VRChat chatbox?"

Domain expert: "No. A peer-channel utterance represents another participant's speech. Its transcript and translation belong in the VR Subtitle Overlay; the VRChat chatbox is for sending the user's own translated speech into VRChat."

Developer: "If a new user chooses Managed Connection, do they need an OpenRouter API key first?"

Domain expert: "No. Managed Connection starts through Discord verification. If managed access succeeds, the broker issues managed translation access; users bring their own key only when they choose a non-managed Translation Connection."
