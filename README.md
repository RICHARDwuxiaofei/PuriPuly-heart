<p align="center">
  <img src="src/puripuly_heart/data/icons/icon.png" alt="PuriPuly <3" width="128" />
</p>

<h1 align="center">PuriPuly <3</h1>

<p align="center">
  <img src="https://img.shields.io/badge/version-1.2.0-blue" alt="Version" />
  <img src="https://img.shields.io/badge/license-MIT-green" alt="License" />
  <img src="https://img.shields.io/badge/python-3.12-yellow" alt="Python" />
  <img src="https://img.shields.io/badge/platform-Windows-lightgrey" alt="Platform" />
</p>

<p align="center">LLM-based real-time translator for VRChat</p>

<p align="center">
  <b>English</b> | <a href="README.ko.md">한국어</a>
</p>

---

## Demo

---

## Finally, talk like real friends.

You've been there.  
Wanting to comfort a friend,  
but only managing — "Are you okay?"

You already know a 'translator'  
can't carry what you really meant to say.

So I built one that can.

- **LLM-based Localization** — Slang, colloquialisms, and casual/formal speech handled naturally.
- **Context Awareness** — Maintains natural conversation flow by considering context (Only in Gemini).
- **Robust Input Handling** — Restores meaning even with typos or truncated text.
- **Personalized Translation** — Define your own specific style in the Prompt Editor.

### [📥 Download](https://github.com/kapitalismho/PuriPuly-heart/releases/latest)

---

## Is there a cost?

You can use it for free over **250,000 times** using free credits provided upon signing up (Deepgram, Google AI Studio).

After the free credits run out, a small fee is charged per utterance.

This app uses cloud AI services. You pay directly for what you use with your own API keys.

---

### Recommended: Deepgram + Gemini (Fast Response)

| Status | Cost/Utterance |
|--------|----------------|
| Using Free Credits | **$0.00** |
| After Free Credits | ~$0.0015 |

* Deepgram offers $200 and Gemini offers $300 in free credits.

---

### Cost per Utterance

| Combo | Full price | Deepgram Free Credit | Gemini Free Credit | Both Free Credits |
| :--- | :--- | :--- | :--- | :--- |
| **Deepgram + Gemini 3 Flash** | ~$0.0015 | ~$0.0007 | ~$0.0008 | $0.00 |
| **Soniox + Gemini 3 Flash** | ~$0.0013 | - | ~$0.0006 | - |
| **Qwen ASR + MT Flash** | ~$0.0006 | - | - | $0.00 |

*   *Qwen API costs represent the Beijing region.*
*   *Soniox charges per connection time.*
*   *Based on: Input 850 tokens + Output 20 tokens x avg 1.3 LLM calls per utterance.*
*   *Based on pricing as of Feb 8, 2026 / Fast Response mode active.*

---

### Free Credits

| Service | Free Credit | Duration | Note |
|--------|------------|------|------|
| **Deepgram** | $200 | None | - |
| **Gemini** | $300 | 90 days | Received after upgrading to paid tier |
| **Qwen** | 1M tokens/model | 90 days | Singapore region |

---

# If API keys are new to you, follow the [Guide](#api-key-setup-guide).

## Usage

1. Download and install the latest version from the [Download Page](https://github.com/kapitalismho/PuriPuly-heart/releases/latest).
2. Get a Deepgram API Key from [Deepgram](https://console.deepgram.com).
3. Get a Gemini API Key from [Google AI Studio](https://aistudio.google.com/apikey).
4. **Upgrade Gemini API plan** to paid tier (Optional but recommended).
5. Enter and verify API keys in the PuriPuly **Settings** tab.
  - Paste the key and press Enter or unfocus the input box.
6. Select Source/Target languages on the **Dashboard**.
7. Click **STT** and **Trans** buttons.
8. Enable OSC in VRChat: Settings → OSC → Enable.

* If voice is not recognized, select the correct microphone in the Settings tab.

---

### Note for Users in China

If Gemini/Deepgram/Soniox are blocked in your region:

1. Get an API Key from [Alibaba Cloud Model Studio](https://bailian.console.alibabacloud.com) (Select Beijing region for the key).
2. Change providers in **Settings**:
   - STT: **Qwen ASR**
   - LLM: **Qwen MT Flash**
   - Qwen Server Region: **Beijing**
3. Enter and verify your Qwen API key (Beijing).

---

## API Key Setup Guide

<details>
<summary><h3>Deepgram</h3></summary>

1. Login to the [Deepgram Console](https://console.deepgram.com/).
   ![step1](docs/images/deepgram/1.png)

2. If you see a welcome message/survey, verify and click **Skip**.
   ![step2](docs/images/deepgram/2.png)

3. Select **STT (Speech-to-Text)** on the service selection screen.
   ![step3](docs/images/deepgram/3.png)

4. In the API Keys menu, click **Create a New API Key**.
   ![step4](docs/images/deepgram/4.png)

5. Enter a key name (e.g., `puripuly`) and create.
   ![step5](docs/images/deepgram/5.png)

6. Copy the generated key and paste it into PuriPuly settings.
   ![step6](docs/images/deepgram/6.png)

</details>

<details>
<summary><h3>Gemini</h3></summary>

1. Go to [Google AI Studio](https://aistudio.google.com/apikey) and click the **Get API key** button.
   ![step1](docs/images/gemini/1.png)

2. Create a new project.
   ![step2](docs/images/gemini/2.png)

3. Choose any name for the project.
   ![step3](docs/images/gemini/3.png)

4. Select the project you created and click **Create key**.
   ![step4](docs/images/gemini/4.png)

5. Click the circled area.
   ![step5](docs/images/gemini/5.png)

6. Click the circled area to copy the key.
   ![step6](docs/images/gemini/6.png)

7. (Highly Recommended) Click the yellow **Set Up Billing** button to upgrade to the paid tier.
   ![step7](docs/images/gemini/7.png)

</details>

<details>
<summary><h3>Qwen</h3></summary>

1. Login to [Alibaba Cloud Model Studio](https://bailian.console.alibabacloud.com). Make sure to select the correct Region for your API key (e.g., Beijing).
   ![step1](docs/images/qwen/1.png)

2. Click the **gear icon** in the top right.
   ![step2](docs/images/qwen/2.png)

3. Create a workspace and go to the **API-KEY** page.
   ![step3](docs/images/qwen/3.png)

4. Click **Create API Key**.
   ![step4](docs/images/qwen/4.png)

5. Assign an account and workspace, then click OK.
   ![step5](docs/images/qwen/5.png)

6. Click the circled area to copy the key.
   ![step6](docs/images/qwen/6.png)

</details>

<details>
<summary><h3>Soniox</h3></summary>

1. Login to [Soniox Console](https://console.soniox.com/).
   ![step1](docs/images/soniox/1.png)

2. Enter an organization name of your choice.
   ![step2](docs/images/soniox/2.png)

3. Click **Add Funds** to link a payment method.
   ![step3](docs/images/soniox/3.png)

4. Soniox requires prepaid credits. Once added, go to the **API Keys** menu.
   ![step4](docs/images/soniox/4.png)

5. Create a new API Key.
   ![step5](docs/images/soniox/5.png)

6. Copy the generated key and paste it into PuriPuly settings.
   ![step6](docs/images/soniox/6.png)

</details>


---

## Q&A

- **Voice recognition is poor**
→ Try using Soniox as an alternative. It is especially recommended for Korean speakers.

- **I don't like the translation style**
→ You can define your own style directly in Settings → Prompt Editor.

- **What is Fast Response mode?**
→ It starts translating before your sentence is finished to reduce latency. Switching to Stable mode can verify the full sentence first, potentially saving costs slightly.

- **Spacing or punctuation in voice recognition looks weird**
→ That's fine. LLMs are robust to such noise and handle translation well regardless.

- **Can I use my Gemini subscription instead of an API key?**
→ No. Gemini subscription and API are separate services.

- **Voice text appears but no translation follows.**
→ Did you upgrade your Gemini API to the paid tier? The free tier is limited to 15 requests per minute. Heavy usage can lead to temporary blocks. We recommend using the paid tier.

---

## Development

### Installation

```bash
python -m venv .venv
.venv\Scripts\activate  # Windows
```

```bash
# pip
pip install -e '.[dev]'

# or uv
uv sync --dev
```

```bash
pre-commit install
```

### Running

```bash
# After activating venv
python -m puripuly_heart.main run-gui

# or using uv run directly
uv run python -m puripuly_heart.main run-gui
```

### Testing & Linting

```bash
black src tests          # Format
ruff check src tests     # Lint
python -m pytest         # Test (recommended within venv)
```

### Build

```bash
.venv\Scripts\pyinstaller build.spec   # Executable
ISCC installer.iss       # Installer
```
---

## Developer

[salee](https://github.com/kapitalismho)

---

## Contributors

[RICHARDwuxiaofei](https://github.com/RICHARDwuxiaofei)

---

## License

[MIT](LICENSE)

Third-party licenses: `THIRD_PARTY_NOTICES.txt`
