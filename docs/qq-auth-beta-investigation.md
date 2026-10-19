# QQ Auth Beta investigation

## Fixed references

Investigation started on 2026-10-19 (Asia/Shanghai), on the Windows desktop in a fresh child checkout. The readiness branch resolved remotely to `76af91bb76deabcbd0f8dc519b5e21f2049054df`, with parent `683b23609b22d439111ffb8d9cdb2740d43f260c`. The previous published Beta resolves to `86d0e698de7cefb8b6360f8e119ec63beb4ae2b0`. Upstream PR 262 is merged at `f28891da7f75508d4e3bcbf1bddbbca99ac7616d`. The upstream checkout fetched during this investigation had dev at `bc9eb4889c8914f317446e97c881325ddf50034f`. This work starts from readiness, preserving its desktop chat, model management, and historical wrist source.

## Confirmed client defects

Before the repair, a new settings file through `ManagedAuthRuntimeAdapter.execute_qq` fails with `qq_managed_operation_installation_unavailable`, before any assert HTTP request. The QQ path never initializes the fresh installation identity, whereas the Discord path has identity preflight. A focused regression first failed against the fixed readiness baseline, then passed after adding fresh-only identity initialization at the existing composition boundary. Existing pending operations and deliveries must never trigger identity regeneration.

The QQ assertion parser accepted HTTP 200 with `ok=false`, `status=issued`, and a non-empty key. That regression also failed before the repair. The client now requires business success, and modern operations require the delivery ACK contract. ACK already required `ok=true` plus `acknowledged` or `already_acknowledged` in readiness; the original Python utility only checked ACK HTTP success. Unknown ACK statuses and redirects are rejected.

Damaged pending operation identity or a missing resume token previously cleared the pending operation and could generate another assertion. Separate regressions reproduced both paths. The repair retains the pending operation and reports a local recovery error. An uncertain assertion followed by UNKNOWN_OPERATION retains the original operation; it does not create another issuance operation. Explicit terminal failures cannot authorize an unconfirmed key.

Key and delivery metadata were persisted separately, and some settings failures discarded the recovery metadata before the key had been durably bound. A private SecretStore receipt now binds key digest, source, installation, operation, credential reference, and delivery. It contains the ACK token only inside SecretStore. The receipt is durable before proceeding, and its confirmation is durable before final cleanup. A failed settings commit can reconstruct the same pending delivery. Actual encrypted storage and settings persistence were exercised with failures before ACK and after ACK; both recovered with one assertion total.

Execution resolution, including the real provider factory, rejects a pending or mismatched modern QQ receipt. QQ Auth Beta does not admit a bare legacy key without a receipt. QQ, Discord, and BYOK keep separate secret slots. The saved receipt is not an online key validity claim; provider creation, actual translation, and usage are separate outcomes. Successful auth requests a runtime rebuild even when an earlier provider object exists. Provider rebuild failure remains visible independently of the authentication commit.

Encrypted SecretStore instances now reload the current encrypted item map under a shared path lock before each mutation. This prevents unrelated concurrent settings/auth secret writes from overwriting each other. Keyring operations use a service lock. The existing managed identity persistence callback is used to preserve admitted concurrent changes.

## Request and state comparison

| Stage | Supplied Python utility contract | Published Beta / readiness App | This channel | PR 262 reference |
| --- | --- | --- | --- | --- |
| Destination | default production Broker, `/v1/auth/qq/assert` | configured Broker, same `/v1` path | same contract, no production deployment | requires coordinated Broker/client rollout |
| Request | identity, HMAC credential, UTC timestamp, ACK support | adds installation, operation, resume token; optional referral | retains operations, recovery, ACK and optional referral | removes referral features, passes installation independently |
| Network | urllib, 15 s, explicit UA/Accept/JSON | httpx, 10 s, application proxy/TLS selection and cached client | same App client and policy; logs only safe route/TLS classification | wider network and diagnostics changes |
| Before HTTP | none of the App's local state steps | identity/token/settings storage can fail | fresh identity preflight and retained failure state | identity and state recovery fixes |
| Response | key presence after HTTP 2xx | more entitlement fields and recovery semantics | strict business response and modern delivery contract | stricter delivery binding |
| Persistence | no App profile, keyring, or settings | separate key, ACK, operation and settings writes | durable private bound receipt plus existing secret slots | bound credentials and transaction admission |
| ACK | does call ACK; checks HTTP only | strict HTTP/business confirmation | preserves strict ACK, validates binding and retains restart recovery | terminal/cleanup recovery and server coordination |
| Provider | none | real runtime must read active source and initialize | real provider factory uses confirmed credential resolution | retry translation after saving credentials |
| Usage | none | can fail after auth; readiness added terminal failure handling | preserves readiness distinction and adds optional read-only probe | additional recovery/state changes |
| GUI | none | can show a later runtime or usage failure | distinct QQ Auth Beta title and safe correlated stages | broader UI changes, not merged wholesale |

The original Python utility's success therefore does not exercise the failing fresh-App preflight, persistent operation recovery, keyring/settings commit, active provider, or usage path. No production account was issued a key during this investigation. These reproduced client defects are not proof of the feedback users' exact individual server state.

## Broker boundary

The old Broker source passes `passConfig.enabled ? installationId : null` as `referredInstallationId`, which is also used by operation binding downstream. PR 262 separates this identity and removes the referral system. This is a conditional compatibility risk, not a confirmed production root cause. A new null-bound operation may still be created; existing operation recovery and setting changes must be tested separately. This machine has no available Linux-native WSL workspace or Docker command, and repository guidance restricts Broker Node verification to Linux-native dependencies. No Broker files were modified, no production databases/keys/entitlements were read or mutated, and no original-repository PR/comments were created. The production deployment revision is unknown.

If the old coupling is confirmed in a controlled matching Broker environment, the minimal server recommendation is to pass the actual installation identity independently of referral enablement, preserve `/v1` operation and resume-token validation, and handle existing null-bound operations through an explicit verified recovery policy. Do not bulk reset claims or authorize mismatched bindings. Test and deploy the compatible server contract first, then retest this client with one explicitly confirmed user attempt. This repository does not deploy that change.

Selective references from PR 262: fresh QQ identity preflight, durable credential/delivery binding, terminal credential safety, retaining saved credentials after settings failure, preserving concurrent admitted state, and runtime retry after a saved key. Referral removal, Broker migrations/cleanup ownership, server issuance/reconciliation changes, and the broader UI/settings rewrite were not merged. The client's existing referral contract is preserved.

## Channel identities and ownership

| Channel | Installer identity | Program directory | Data / keyring |
| --- | --- | --- | --- |
| Installed original 2.4.0 | `{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}_is1` | LocalAppData/Programs/PuriPulyHeart | puripuly-heart |
| Installed Desktop Beta | `PuriPulyHeart-Fork-Desktop-Beta_is1` | LocalAppData/Programs/PuriPulyHeartBeta | PuriPulyHeartBeta / puripuly-heart.test.desktop-beta |
| Readiness candidate | portable isolated profile, no new installer identity in its source | caller-selected portable directory | isolated-data / puripuly-heart.test.readiness-20261018 |
| Wrist development | portable development flag | caller-selected portable directory | wrist-development-data / puripuly-heart.test.desktop-wrist-vision |
| This channel | `PuriPulyHeart-Fork-QQ-Auth-Beta_is1` | LocalAppData/Programs/PuriPulyHeartQQAuthBeta | PuriPulyHeartQQAuthBeta / puripuly-heart.test.qq-auth-beta |

Only the last installer identity is owned by this work. AppId stays fixed for future upgrades. The installer accepts only the exact normalized default per-user directory and rejects links, unknown existing directory ownership, alternative/nested locations, and active QQ Auth Beta instances. It does not register a shared updater, protocol, autorun, or a fixed control port. Existing control instance identity is based on the dedicated settings path; native workers use their existing owner/child lifecycle. No process-name kills are introduced.

Uninstall uses only its own Inno registration and the installed-file manifest. There is no `[UninstallDelete]`, no `{app}\*` cleanup, and no call to another channel's uninstaller. Default data retention includes the new channel's recovery identity and credentials. Optional cleanup requires a matching data marker, rejects reparse points, deletes only exact inventoried keys in this channel's namespace, and does not traverse external model directories. The original generic installer, which contains original-data cleanup, is not used for this channel.

## Diagnostics and verification boundaries

`tools/qq_auth_probe.py` and the bundled Chinese launcher default to local-only synthetic testing. App mode invokes the same `ManagedAuthRuntimeAdapter`, Broker client/network policy, SecretStore factory, managed state persistence callback and credential resolver/provider factory as the GUI. It accepts a real identifier and an existing credential only after interactive confirmation. It never generates production identifiers/PSKs, loops production issuance, prints keys, or silently changes auth source. Recovery-only mode refuses to create a new operation.

The offline probe uses real loopback HTTP, independent fresh states per scenario, actual settings and encrypted/keyring storage, child process restart, and the real provider's primary translation implementation pointed explicitly at the controlled service. Automatic local translation never targets a paid endpoint. A separate full headless App composition integration tests GUI-equivalent auth ownership with all external HTTP refused. These are distinct from GUI interaction and installed-package verification. The Linux-only matched historical Broker invitation matrix remains unverified.

Protected programs/data were hashed read-only before installation: original program 1391 files, Desktop Beta program 16392 files, original data 206 files, Desktop Beta data 16 files. Only aggregate hashes and counts belong in public evidence, not real settings, secret material, or private logs. Final installer execution evidence, exact package SHA256, GUI observations, protection rechecks and release status are recorded in the release-side TEST-RESULTS and BUILD attachments after building this source.

C/D remain outside a claim of full repair: retain readiness Peer terminal cleanup and ASR phase diagnostics; real peer audio, device/model acceptance, and an initialization call that never returns are not proven fixed by these changes.
