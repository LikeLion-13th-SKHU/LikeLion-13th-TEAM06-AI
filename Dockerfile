FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# (필요시 apt 패키지 추가)
RUN apt-get update && apt-get install -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# API 의존성 설치
COPY requirements-api.txt .
RUN pip install --no-cache-dir -r requirements-api.txt

# 앱 전체 복사 (파이프라인 + API)
COPY . .

# (선택) 파이프라인용 추가 의존성이 있다면 여기서 설치
# 예: COPY requirements.txt . && RUN pip install --no-cache-dir -r requirements.txt

EXPOSE 8000
CMD ["uvicorn","app:app","--host","0.0.0.0","--port","8000","--workers","2"]
