# 📰 AI 뉴스 요약·분류 시스템

이 프로젝트는 \*\*LLM API(Groq)\*\*와 **FastAPI**를 활용하여 뉴스를 **자동으로 요약하고 카테고리 및 지역 정보를 분류**하는 AI 파이프라인입니다.
입력된 JSON 데이터를 처리하여 요약·카테고리·지역을 포함한 새로운 JSON 파일을 생성합니다.

---

## 👤 MADE BY

* **김현민**

---

## ✅ 주요 기능

* 뉴스 기사 **본문 요약 (3줄 이내)**
* **카테고리 분류** (정책/정부, 산업/기업, 기술/R\&D, 사회 등)
* 기사에서 **지역 추출** (서울/부산/전국 등)
* 입력 데이터(`input.json`) → 결과 데이터(`output.json`) 자동 변환
* **Docker + FastAPI 서버**로 API 제공
* HTML 태그 자동 제거 및 전처리 지원

---

## 📦 설치 방법

1. 레포 클론

```bash
git clone https://github.com/your-username/ai-news-analyzer.git
cd ai-news-analyzer
```

2. 필수 패키지 설치

```bash
pip install -r requirements.txt
```

---

## ⚙️ 환경 설정 (.env 파일)

1. `.env.example` 파일을 복사하여 `.env` 파일 생성

```bash
cp .env.example .env
```

2. 아래 정보를 입력

```env
GROQ_API_KEY=your_api_key_here
MODEL=llama-3.1-8b-instant
```

---

## ▶️ 실행 방법

### 1) 로컬 실행

```bash
python run_all.py input.json output.json
```

* `input.json` : 원본 뉴스 데이터
* `output.json` : 요약 및 분류 결과

### 2) FastAPI 서버 실행

```bash
uvicorn app:app --reload --host 0.0.0.0 --port 8000
```

API 엔드포인트 예시:

```bash
POST http://localhost:8000/run/json
Content-Type: application/json

{
  "items": [
    {
      "newsIdentifyId": "148946998",
      "title": "테스트 제목",
      "contents": "여기는 본문 내용입니다"
    }
  ]
}
```

---

## 📂 GitHub에 포함되지 않는 파일

`.gitignore`에 의해 아래 파일은 업로드되지 않습니다:

* `.env` (API 키 보안)
* `__pycache__/` (파이썬 캐시)
* `input.json` / `output.json` (실행 데이터)
* `*.log` (실행 로그)

---

## 💡 확장 아이디어

* 뉴스 데이터 **실시간 API 크롤링 연동**
* **웹 대시보드 (Streamlit/Gradio)** 추가
* 더 정교한 **세부 카테고리 분류**
* 성능 비교를 위한 **다중 모델 지원 (GPT, Claude 등)**

---

## 🧠 라이선스 & 출처

* LLM API: [Groq](https://groq.com/)
* Framework: [FastAPI](https://fastapi.tiangolo.com/)
* Docker 기반 배포 지원

---
