# Cloud Run Job 실행 이미지 — 파이프라인 로직(main.py/src/)은 무변경, cloud/만 신규.
# 설계 근거: 03-설계/설계서.md "(인프라 이관) Cloud Run 마이그레이션 설계" N-F절.
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .
COPY src/ src/
COPY config/ config/
COPY cloud/ cloud/

ENTRYPOINT ["python", "cloud/entrypoint.py"]
