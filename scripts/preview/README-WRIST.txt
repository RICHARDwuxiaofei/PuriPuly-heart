PuriPuly Heart - Wrist Vision local development build

This is an unpackaged-hardware-acceptance development build, NOT a formal Release.
Double-click PuriPulyHeartPreview.exe. No separate Python installation is required.
Do not copy it over an existing PuriPuly installation.

This build uses wrist-development-data next to the launcher and the isolated
puripuly-heart.test.desktop-wrist-vision secret-store namespace. It does not reuse
production or desktop-beta credentials, nor environment API keys.
Enter your DeepSeek key once in this isolated profile if you want a real vision test.
The feature then reuses that existing application credential; it has no second key form.
No model, user key, personal screenshot, room audio or production settings are bundled.

Open Settings > General > Wrist Vision, or use the VR icon in the title bar.
Set the preferred hand, position, width, opacity and display modes. Apply / Start.
The wrist feature defaults OFF and is independent from the existing subtitle overlay.
Current builds start the wrist process when you explicitly click Apply / Start;
merely having a saved enabled preference does not start capture or upload anything.

In VR: aim the OTHER controller at a wrist button and use its trigger.
Choose Translate Screenshot, aim the frame, release and pull the trigger again.
Grip/menu cancels. If wrist aiming is awkward, choose the head-relative frame.
Only confirming a screenshot can upload the selected compositor-eye region to DeepSeek.
This may incur API fees. There are no automatic paid retries or background captures.
Images stay in memory and are discarded on completion/cancel/close.

Spatial patches are NOT IMPLEMENTED. The experimental option explicitly falls back
to wrist reading; if fallback is disabled, the unavailable mode fails before spending.
No game materials are modified and no claim of accurate spatial replacement is made.
New controls have Chinese/English strings; other current locales use English fallback.

PASS: Mock/native-process contracts and local builds as recorded in the development report.
NOT_TESTED: worn headset, real controller feel, actual eye-mirror screenshots in VRChat,
paid API calls, spatial precision, long gaming sessions and interactive GUI acceptance.
Render previews marked MOCK are synthetic GDI panels, NOT VR hardware screenshots.

Failure diagnostics: normal Logs page and rotating application log, module [WristVision].
Never send API keys or private scene images as part of a routine bug report.
BUILD.json and SOURCE.zip identify the exact development source/components.
