# Cloud Run Job 실행 이미지 — 파이프라인 로직(main.py/src/)은 무변경, cloud/만 신규.
# 설계 근거: 03-설계/설계서.md "(인프라 이관) Cloud Run 마이그레이션 설계" N-F절.
FROM python:3.11-slim

# 리포트 기준 시간은 KST(Asia/Seoul)이다. main.py/src/의 datetime.now() 호출은
# 원래 실행 환경(로컬 macOS, 시스템 tz=KST)에서 암묵적으로만 KST로 맞아떨어졌는데,
# Cloud Run 컨테이너 기본 tz(UTC)로 옮기며 그 전제가 깨져 자정 부근 실행 시 날짜가
# 하루 밀리는 문제가 발생했다(리포트 파일명 등). 파이프라인 코드는 건드리지 않고,
# 로컬에서 암묵적으로 성립하던 KST 가정을 컨테이너 tz로 명시 고정해 맞춘다.
RUN apt-get update && apt-get install -y --no-install-recommends tzdata \
    && ln -sf /usr/share/zoneinfo/Asia/Seoul /etc/localtime \
    && echo "Asia/Seoul" > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*
ENV TZ=Asia/Seoul

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .
COPY src/ src/
COPY config/ config/
COPY cloud/ cloud/

ENTRYPOINT ["python", "cloud/entrypoint.py"]
