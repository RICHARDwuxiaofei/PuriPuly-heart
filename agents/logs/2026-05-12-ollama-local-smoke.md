# Ollama Local LLM Smoke Test

## Verification level

L1 - local Ollama server/API and app Local OpenAI-compatible provider smoke checks.

## Commands run

- `ollama --version`
- `ollama list`
- `Invoke-RestMethod http://localhost:11434/api/tags`
- `Invoke-RestMethod http://localhost:11434/v1/chat/completions` with model `gemma4:e2b`
- `.venv\Scripts\python.exe -` smoke script using `LocalOpenAICompatibleLLMProvider`
- `.venv\Scripts\python.exe -` diagnostic script for `HttpxLocalOpenAIClient` verifier-style calls
- `.venv\Scripts\python.exe -` local OpenAI-compatible proxy smoke requiring `Authorization: Bearer test-local-api-key` and forwarding to Ollama

## Outcome

- PASS: Ollama is installed: `0.23.2`.
- PASS: Ollama server responds on `http://localhost:11434`.
- PASS: installed model detected: `gemma4:e2b`.
- PASS: OpenAI-compatible `/v1/chat/completions` endpoint returned `PONG` content for `gemma4:e2b`.
- PASS: app Local LLM provider translated `안녕하세요` to `Hello` with `gemma4:e2b`.
- PASS: API-key path works through a local Bearer-gated OpenAI-compatible proxy in front of Ollama:
  - correct key sent `Authorization: Bearer test-local-api-key` and translated successfully: `Hello`;
  - empty key sent no Authorization header and failed with 401;
  - wrong key sent `Authorization: Bearer wrong-key` and failed with 401.
- FAIL/diagnostic: current `LocalOpenAICompatibleLLMProvider.verify_connection()` returned `False` for `gemma4:e2b` because verifier-style calls use `max_tokens=1`; this model reports `finish_reason=length` for the probe prompt. With no `max_tokens`, the same client call succeeds.

## Skipped items

- GUI manual clicking was not performed.
- Default model `llama3.1:8b` was not tested because it is not installed in this Ollama instance.

## Notes

- For this environment, set the Local LLM model ID to `gemma4:e2b` rather than the app default `llama3.1:8b` unless that default model is pulled first.
