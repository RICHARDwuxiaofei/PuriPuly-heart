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
  <a href="README.md">English</a> | <b>한국어</b>
</p>

---

## Demo

---

## Finally, talk like real friends.

위로하고 싶었는데  
"괜찮아?"밖에 못 건넨 적 있잖아요.

전하고 싶은 마음이  
'번역기'로는 안 되는 거 알잖아요.

그래서 만들었어요.

- **LLM 기반 현지화** — 슬랭, 구어체, 반말/존댓말까지 자연스럽게
- **맥락 기억** — 문맥를 고려한 자연스러운 대화 흐름 유지 (Only in Gemini)
- **거친 입력도 OK** — 띄어쓰기가 잘못 되어도, 글자가 하나씩 잘려도 복원 가능
- **내 말투로 번역** — 프롬프트 에디터에서 스타일 직접 지정

### [📥 다운로드](https://github.com/kapitalismho/PuriPuly-heart/releases/latest)

---

## 사용하는데 돈이 드나요?

신규 가입(Deepgram, Google AI studio) 시 제공되는 무료 크레딧으로 **약 25만 회까지 무료**로 사용할 수 있어요.

무료 크레딧을 모두 사용한 이후에는 발화 한 번에 소량의 요금이 부과되어요.

이 앱은 클라우드 AI 서비스를 활용해요. 사용자가 발급받은 API 키로 사용한 만큼만 직접 과금되는 구조에요.

### 추천 조합: Deepgram + Gemini (빠른 응답)

| Status | Cost/Utterance |
|--------|----------------|
| 무료 크레딧 사용 | **$0.00** |
| 크레딧 소진 후 | ~$0.0015 (2.3원) |

* Deepgram은 $200, Gemini는 $300 무료 크레딧을 제공해요.
---

### 발화당 비용

| Combo | Full price | Deepgram Free Credit | Gemini Free Credit | Both Free Credits |
| :--- | :--- | :--- | :--- | :--- |
| **Deepgram + Gemini 3 Flash** | ~$0.0015 (2.3원) | ~$0.0007 (1.0원) | ~$0.0008 (1.2원) | $0.00 |
| **Soniox + Gemini 3 Flash** | ~$0.0013 (1.9원) | - | ~$0.0006 (0.9원) | - |
| **Qwen ASR + MT Flash** | ~$0.0006 (0.9원) | - | - | $0.00 |

*   *Qwen의 API 비용은 베이징 리전 기준*
*   *Soniox는 연결 시간당 과금이 발생*
*   *입력 850 토큰 + 출력 20토큰 x 발화 1회당 평균 LLM 호출 횟수 1.3회 가정*
*   *요금표 기준: 2026년 2월 8일 / 빠른 응답 모드 활성화*
*   *1 달러 = 1500원*

---

### 무료 크레딧

| 서비스 | 무료 크레딧 | 기한 | 비고 |
|--------|------------|------|------|
| **Deepgram** | $200 | 없음 | - |
| **Gemini** | $300 | 90일 | 유료 티어로 전환 후 받음 |
| **Qwen** | 모델당 100만 토큰 | 90일 | 싱가포르 리전 기준|

---

# API 키가 생소하다면 [가이드](#api-키-발급-가이드)를 보고 따라해주세요

## 사용법

1. [다운로드 페이지](https://github.com/kapitalismho/PuriPuly-heart/releases/latest)에서 최신 버전 다운로드 및 설치
2. [Deepgram](https://console.deepgram.com)에서 Deepgram API 키 발급
3. [Google AI Studio](https://aistudio.google.com/apikey)에서 Gemini API 키 발급
4. **Gemini API 결제 플랜**을 유료로 전환 (선택 사항이지만 권장)
5. PuriPuly **설정** 탭에서 API 키 입력 후 검증
  - 입력 칸에 API 키를 붙여넣기 한 후 엔터를 누르거나 포커스를 해제해주세요.
6. **대시보드**에서 원본/대상 언어 선택
7. **STT**와 **Trans** 버튼 클릭
8. VRChat에서 OSC 활성화: Settings → OSC → Enable

* 음성이 인식되지 않는다면 설정 탭에서 올바른 마이크를 선택해주세요.

---

### 중국 사용자를 위한 안내

Gemini/Deepgram/Soniox가 차단된 지역이라면:

1. [Alibaba Cloud Model Studio](https://bailian.console.alibabacloud.com)에서 API 키 발급 (베이징 리전 API 키를 발급 받으세요)
2. **설정**에서 제공자 변경:
   - STT: **Qwen ASR**
   - LLM: **Qwen MT Flash**
   - Qwen 서버 리전: **Beijing**
3. Qwen API 키 (Beijing) 입력 후 검증

---

## API 키 발급 가이드

<details>
<summary><h3>Deepgram</h3></summary>

1. [Deepgram Console](https://console.deepgram.com/)에 접속하여 로그인하세요.
   ![step1](docs/images/deepgram/1.png)

2. 가입 환영 메시지 및 설문이 나오면 **Skip**을 눌러 건너뛰세요.
   ![step2](docs/images/deepgram/2.png)

3. 서비스 선택 화면에서 **STT (Speech-to-Text)**를 선택하세요.
   ![step3](docs/images/deepgram/3.png)

4. API Keys 메뉴에서 **Create a New API Key**를 클릭하세요.
   ![step4](docs/images/deepgram/4.png)

5. 키 이름을 입력하고(예: `puripuly`) 생성하세요.
   ![step5](docs/images/deepgram/5.png)

6. 생성된 키를 복사하여 PuriPuly 설정에 붙여넣으세요.
   ![step6](docs/images/deepgram/6.png)

</details>

<details>
<summary><h3>Gemini</h3></summary>

1. [Google AI Studio](https://aistudio.google.com/apikey)에 접속하세요.
   ![step1](docs/images/gemini/1.png)

2. **Get API key** 버튼을 클릭하세요.
   ![step2](docs/images/gemini/2.png)

3. **Create API key** 버튼을 누르세요.
   ![step3](docs/images/gemini/3.png)

4. **Create API key in new project**를 선택하세요.
   ![step4](docs/images/gemini/4.png)

5. 생성된 키를 복사하세요.
   ![step5](docs/images/gemini/5.png)

6. PuriPuly의 **설정** 탭에서 Gemini API 키 입력칸에 복사한 키를 붙여넣으세요.
   ![step6](docs/images/gemini/6.png)

7. (매우 권장) 노란색으로 강조된 **Set Up Billing** 버튼을 눌러 결제 계정을 연결하세요.
   ![step7](docs/images/gemini/7.png)

</details>

<details>
<summary><h3>Qwen</h3></summary>

1. [Alibaba Cloud Model Studio](https://bailian.console.alibabacloud.com)에 로그인 하세요. 본인이 API 키를 발급받으려는 리전(Region)을 정확히 선택해주세요. (예: Beijing)
   ![step1](docs/images/qwen/1.png)

2. 우측 상단의 **톱니바퀴 아이콘**을 클릭하세요.
   ![step2](docs/images/qwen/2.png)

3. 워크스페이스를 생성하고 **API-KEY** 페이지로 넘어가세요.
   ![step3](docs/images/qwen/3.png)

4. **Create API Key**를 클릭하세요.
   ![step4](docs/images/qwen/4.png)

5. 생성된 키를 복사하세요.
   ![step5](docs/images/qwen/5.png)

6. PuriPuly의 **설정** 탭에서 Qwen API 키(Beijing) 입력칸에 붙여넣으세요.
   ![step6](docs/images/qwen/6.png)

</details>

<details>
<summary><h3>Soniox</h3></summary>

1. [Soniox Console](https://console.soniox.com/)에 로그인하세요.
   ![step1](docs/images/soniox/1.png)

2. 조직 이름을 임의로 적어주세요.
   ![step2](docs/images/soniox/2.png)

3. **Add Funds** 버튼을 눌러 결제 수단을 연결하세요.
   ![step3](docs/images/soniox/3.png)

4. 소니옥스는 선불금 충전이 필요합니다. 충전이 완료되면 **API Keys** 메뉴로 이동하세요.
   ![step4](docs/images/soniox/4.png)

5. 새로운 API Key를 생성하세요.
   ![step5](docs/images/soniox/5.png)

6. 생성된 키를 복사하여 PuriPuly 설정에 붙여넣으세요.
   ![step6](docs/images/soniox/6.png)

</details>


---

## Q&A

- **음성 인식이 잘 안 돼요**
→ 대안으로 Soniox를 사용해보세요. 특히 한국어 사용자에게 추천해요.

- **번역 말투가 마음에 안들어요**
→ 설정 → 프롬프트 에디터에서 원하는 말투를 직접 지정할 수 있어요.

- **빠른 응답 모드는 뭐가 달라요?**
→ 말이 끝나기 전에 미리 번역을 시작해서 지연시간을 줄여줘요. 안정 모드로 전환하면 비용을 약간 아낄 수 있어요.

- **음성 인식에서 띄어쓰기나 문장부호가 이상하게 나와요**
→ 괜찮아요. LLM은 이러한 노이즈 처리에 강해서 번역엔 거의 영향이 없어요.

- **Gemini 유료 구독자인데 API 키 대신 제 구독제를 쓸 수 있나요?**
→ 아니요. Gemini 구독과 API는 별개예요.

- **음성 인식된 텍스트는 나오는데 번역이 안나와요.**
→ Gemini API를 유료로 전환했나요? 무료 티어는 분당 요청 수가 15회로 제한되어 있어요. 요청이 많으면 일시적으로 차단될 수 있어요. 유료 티어로 사용하는 것을 권장해요.

---

## 개발

### 설치

```bash
python -m venv .venv
.venv\Scripts\activate  # Windows
```

```bash
# pip
pip install -e '.[dev]'

# 또는 uv
uv sync --dev
```

```bash
pre-commit install
```

### 실행

```bash
# 가상환경 활성화 후
python -m puripuly_heart.main run-gui

# 또는 uv run으로 바로 실행
uv run python -m puripuly_heart.main run-gui
```

### 개발

```bash
black src tests          # 포맷
ruff check src tests     # 린트
python -m pytest         # 테스트 (가상환경에서 실행 권장)
```

### 빌드

```bash
.venv\Scripts\pyinstaller build.spec   # 실행 파일
ISCC installer.iss       # 인스톨러
```
---

## 개발자

[salee](https://github.com/kapitalismho)

---

## 기여자

[RICHARDwuxiaofei](https://github.com/RICHARDwuxiaofei)

---

## 라이센스

[MIT](LICENSE)

타사 라이센스: `THIRD_PARTY_NOTICES.txt`
