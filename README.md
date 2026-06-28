# AI-Morning-Brief

매일 아침 RSS 피드를 수집하고 Claude로 분석해 Discord + 로컬 Markdown 리포트를 생성하는 자동화 파이프라인.

---

## 목차

1. [요구 사항](#요구-사항)
2. [설치](#설치)
3. [환경 설정](#환경-설정)
4. [소스 설정](#소스-설정)
5. [최초 실행 전 소스 진단](#최초-실행-전-소스-진단)
6. [모델명 설정](#모델명-설정)
7. [파이프라인 테스트](#파이프라인-테스트)
8. [V0.1 성공 기준 (4개 명령)](#v01-성공-기준)
9. [launchd 스케줄 등록 (macOS)](#launchd-스케줄-등록-macos)
10. [CLI 옵션](#cli-옵션)
11. [문제 해결](#문제-해결)

---

## 요구 사항

- macOS (launchd 스케줄러 이용 시)
- Python 3.11 이상
- Anthropic API 키 (Claude 분석 사용 시 — 없으면 fallback 분석으로 자동 대체)
- Discord Webhook URL (선택 — `--no-discord` 로 생략 가능)

---

## 설치

```bash
# 프로젝트 폴더로 이동
cd projects/AI-Morning-Brief/05-개발

# 의존성 설치
pip install -r requirements.txt

# pytest 설치 (통합 테스트 실행 시 필요)
pip install pytest
```

---

## 환경 설정

```bash
# .env 파일 생성 후 실제 키 입력
cp config/.env.example .env
```

`.env` 파일을 열어 아래 값을 채웁니다:

```
ANTHROPIC_API_KEY=sk-ant-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
```

- `ANTHROPIC_API_KEY` 가 없거나 잘못된 경우 Claude 분석이 자동으로 fallback(키워드 기반 요약)으로 대체됩니다. 파이프라인은 중단되지 않습니다.
- `DISCORD_WEBHOOK_URL` 이 없으면 Discord 전송을 건너뛰고 로컬 리포트만 저장합니다.

---

## 소스 설정

```bash
# 소스 설정 파일 복사
cp config/sources.example.yaml config/sources.yaml
```

`config/sources.yaml` 을 열어 구독할 RSS 피드 URL을 추가·수정합니다:

```yaml
sources:
  - name: "소스 이름"
    url: "https://example.com/feed.xml"
    enabled: true
```

`enabled: false` 로 설정하면 수집에서 제외되지만 `--check-sources` 진단 표에는 표시됩니다.

---

## 최초 실행 전 소스 진단

소스 설정 후, **실제 파이프라인 실행 전에 반드시** 아래 명령으로 RSS 접근성을 확인합니다:

```bash
python main.py --check-sources
```

- 각 소스의 접근 가능 여부, HTTP 상태, 기사 수를 표로 출력합니다.
- 접근 불가 소스(`OK: False`)는 URL을 수정하거나 `enabled: false` 로 비활성화하세요.
- 이 명령은 DB·파일·Discord 쓰기를 하지 않습니다(읽기 전용 진단).

---

## 모델명 설정

`config/config.yaml` 의 `claude.model` 항목에 사용할 Anthropic 모델명을 지정합니다:

```yaml
claude:
  model: claude-opus-4-5   # ← 사용 가능한 Anthropic 모델명으로 수정 가능
  max_tokens: 4096
```

> 모델명이 잘못된 경우(오타 · 미존재 모델) Claude 분석이 **fallback(키워드 기반 요약)으로 자동 대체**되며 파이프라인은 중단되지 않습니다.
> 사용 가능한 모델명은 [Anthropic 공식 문서](https://docs.anthropic.com/en/docs/about-claude/models) 를 참고하세요.

---

## 파이프라인 테스트

실제 RSS 피드·Claude API·Discord 없이 로컬 fixtures로 파이프라인 전체를 검증합니다:

```bash
python main.py --test --no-discord
```

- `tests/fixtures/sample_articles.json` 6건 기사를 사용합니다.
- API 키 없이도 fallback 분석으로 리포트를 생성합니다.
- 운영 `data/morning_brief.db` · `reports/` 는 변경되지 않습니다.

통합 테스트 일괄 실행 (QA-1~6, 운영 데이터와 완전 격리):

```bash
pytest -q tests/test_pipeline.py
```

---

## V0.1 성공 기준

아래 4개 명령이 **모두 통과**해야 V0.1 합격입니다.

```bash
# 사전 준비
cd projects/AI-Morning-Brief/05-개발
pip install -r requirements.txt
cp config/sources.example.yaml config/sources.yaml   # 최초 1회

# (1) 정상 파이프라인 — 외부 의존 없이 종료코드 0
python main.py --test --no-discord
echo "exit=$?"

# (2) Fallback 경로 — 리포트에 'AI 분석 실패 / 기본 리포트 생성' 포함
python main.py --test --no-discord --force-fallback
echo "exit=$?"

# (3) Dry-run 무상태 — DB·raw·report·Discord 변경 0, 종료코드 0
python main.py --test --dry-run
echo "exit=$?"

# (4) 통합 테스트 — QA-1~6 전부 통과, 운영 데이터 격리 확인
pytest -q tests/test_pipeline.py
```

명령 (3) 실행 후 새 리포트 파일이나 DB 행이 생기지 않아야 합니다(무상태 검증).

---

## launchd 스케줄 등록 (macOS)

매일 04:30에 자동 실행하려면 launchd를 사용합니다.

> **launchd를 선택한 이유**: Mac이 04:30에 슬립 상태여도 깨어난 직후 놓친 작업을 실행합니다. cron은 슬립 중 작업을 영구적으로 건너뛰어 브리핑이 누락될 수 있습니다. catch-up 수집 정책과도 자연스럽게 맞물립니다.

> ⚠️ **중요 — 보호 폴더(TCC) 회피 · 운영 위치 분리**
> macOS는 `~/Desktop`, `~/Documents`, `~/Downloads` 를 개인정보(TCC) 보호 폴더로 취급합니다.
> 프로젝트가 이 폴더 아래에 있으면 launchd가 띄운 python이 파일에 접근하지 못해
> `can't open file ... [Errno 1] Operation not permitted` 로 **매번 실패**합니다.
> (Python에 '전체 디스크 접근'을 줘도 framework python + launchd 조합에서는 잘 적용되지 않습니다.)
>
> **해결**: 정기 실행용 배포본을 보호 폴더 밖(예: `~/AI-Morning-Brief-run/`)에 두고 그 경로로 등록합니다.
> 개발/이슈 작업은 워크샵 위치에서 하고, 운영은 분리된 배포본에서 돌립니다.
>
> | 용도 | 위치 |
> |------|------|
> | 개발(코드·이슈) | `~/Desktop/IT_make_some/projects/AI-Morning-Brief/05-개발/` |
> | 정기 실행(운영 정본) | `~/AI-Morning-Brief-run/` ← `.env`·`config/sources.yaml`·`data/`·`reports/` 의 정본 |
>
> **코드 갱신 후 재동기화** (운영 설정·데이터는 보존):
> ```bash
> rsync -a \
>   --exclude='.env' --exclude='config/sources.yaml' \
>   --exclude='data' --exclude='reports' \
>   "/Users/lsh/Desktop/IT_make_some/projects/AI-Morning-Brief/05-개발/" \
>   ~/AI-Morning-Brief-run/
> ```
> (최초 1회 전체 복사는 `--exclude` 없이 `rsync -a "<개발경로>/" ~/AI-Morning-Brief-run/` 로 .env·DB까지 함께 옮깁니다.)

### 1단계 — plist 절대경로 치환 (보호 폴더 밖 경로로)

`scripts/com.itsangsang.morningbrief.plist` 를 열어 `/ABS/PATH/TO/` 를 실제 절대경로로 교체합니다:

```bash
# 현재 디렉토리 절대경로 확인
pwd
# 예: /Users/me/projects/AI-Morning-Brief/05-개발
```

수정 항목 (모두 **보호 폴더 밖 배포본 경로**로):
- `ProgramArguments` 의 `main.py` 경로 → 예: `~/AI-Morning-Brief-run/main.py`
- `WorkingDirectory` → 예: `~/AI-Morning-Brief-run`
- python3 경로: ⚠️ **`/usr/bin/python3`(시스템 python)에는 의존성이 없어 실패**합니다.
  의존성을 설치한 python의 절대경로를 쓰세요. 확인: `python3 -c "import sys; print(sys.executable)"`
  (예: `/Library/Frameworks/Python.framework/Versions/3.14/bin/python3`, 가상환경이면 `venv/bin/python3`)

### 2단계 — LaunchAgents 폴더에 복사 및 등록

```bash
cp scripts/com.itsangsang.morningbrief.plist ~/Library/LaunchAgents/
# 최신 macOS는 bootstrap 사용 (load 는 구식)
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.itsangsang.morningbrief.plist
launchctl enable gui/$(id -u)/com.itsangsang.morningbrief
```

### 3단계 — 등록 확인 및 수동 실행 테스트

```bash
# 등록 확인
launchctl print gui/$(id -u)/com.itsangsang.morningbrief | grep -iE "state|program|working directory"

# 즉시 실행 테스트 (스케줄 시간을 기다리지 않고 — 실제 수집·Claude·Discord 발생)
launchctl kickstart -k gui/$(id -u)/com.itsangsang.morningbrief

# 로그 확인 (성공 시 out 에 "완료: 신규 N건, 모드 claude")
cat /tmp/morningbrief.out.log
cat /tmp/morningbrief.err.log
```

### 등록 해제 / 재등록

```bash
# 해제
launchctl bootout gui/$(id -u)/com.itsangsang.morningbrief
# 재등록 (plist 수정 후)
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.itsangsang.morningbrief.plist
launchctl enable gui/$(id -u)/com.itsangsang.morningbrief
```

---

## CLI 옵션

```
python main.py [옵션]
```

| 옵션 | 설명 |
|------|------|
| `--test` | 파이프라인 전체를 테스트 모드로 실행. `tests/fixtures/sample_articles.json` 사용, 운영 데이터 비변경 |
| `--dry-run` | 실제 저장/전송 없이 실행 계획과 리포트 미리보기만 출력. DB·raw·리포트·Discord 무변경 |
| `--no-discord` | Discord 웹훅 전송을 생략하고 로컬 파일만 저장 |
| `--force-fallback` | Claude 분석을 강제로 fallback(키워드 기반 요약) 모드로 실행. Claude 연동 실패 검증용 |
| `--from YYYY-MM-DD` | 수집 시작 날짜 (기본: `lookback_hours` 기준 자동 계산) |
| `--to YYYY-MM-DD` | 수집 종료 날짜 (기본: 현재 시각) |
| `--check-sources` | RSS 소스 접근성 진단만 실행하고 종료. DB·파일·Discord 쓰기 없음 |

### 환경변수 경로 override

| 환경변수 | 기본값 (config.yaml) | 설명 |
|----------|----------------------|------|
| `MORNINGBRIEF_DB` | `data/morning_brief.db` | SQLite DB 파일 경로 |
| `MORNINGBRIEF_RAW_DIR` | `data/raw` | raw JSON 저장 디렉토리 |
| `MORNINGBRIEF_REPORTS_DIR` | `reports` | 리포트 저장 디렉토리 |

### config.yaml 파이프라인 설정

| 항목 | 기본값 | 설명 |
|------|--------|------|
| `pipeline.request_timeout_sec` | `15` | 소스별 RSS HTTP 요청 타임아웃 (초) |

---

## 문제 해결

### Claude 분석이 fallback으로 동작한다

- `.env` 의 `ANTHROPIC_API_KEY` 값을 확인하세요.
- `config/config.yaml` 의 `claude.model` 이 유효한 Anthropic 모델명인지 확인하세요. 잘못된 모델명이면 자동으로 fallback 분석으로 대체됩니다.
- fallback 시에도 리포트와 Discord 메시지는 정상 생성됩니다.

### RSS 피드가 수집되지 않는다

```bash
python main.py --check-sources
```

OK 열이 False 인 소스의 URL을 확인하고 수정하세요.

### pytest 가 운영 DB를 변경하지 않는지 확인하는 방법

```bash
cd projects/AI-Morning-Brief/05-개발
BEFORE=$(test -e data/morning_brief.db && stat -f %m data/morning_brief.db || echo none)
pytest -q tests/test_pipeline.py
AFTER=$(test -e data/morning_brief.db && stat -f %m data/morning_brief.db || echo none)
[ "$BEFORE" = "$AFTER" ] && echo "운영 DB 무변경 OK" || echo "운영 DB 변경됨 (이상)"
```

### Discord 메시지가 전송되지 않는다

- `.env` 의 `DISCORD_WEBHOOK_URL` 이 올바른지 확인하세요.
- Discord 전송 실패는 파이프라인을 중단하지 않습니다. 로컬 리포트는 정상 생성됩니다.

### 리포트 경로를 변경하고 싶다

환경변수 override를 사용합니다:

```bash
MORNINGBRIEF_REPORTS_DIR=/path/to/my/reports python main.py --no-discord
```

또는 `config/config.yaml` 의 `paths.reports_dir` 을 수정합니다.

---

## 주간 흐름 리포트 (`--weekly`) 운영

매주 일요일 새벽 4시 40분에 직전 ISO 주차의 흐름 리포트를 자동 생성하고 Discord로 전송합니다.

### 수동 생성

```bash
# 이번 주 자동 산정
python main.py --weekly

# 특정 주 강제 생성
python main.py --weekly --week 2026-W26

# Discord 전송 없이 리포트만 생성
python main.py --weekly --no-discord
```

### launchd 스케줄 등록 (일요일 04:40)

> 일간 잡(04:30)과 10분 차이를 두는 이유: 주간 잡은 직전 토요일까지의 데이터를 집계하는데, 일간 잡이 먼저 그날 마지막 데이터를 저장하고 나서 주간 집계가 읽어야 안전합니다. 경합이 우려되면 plist의 Minute를 50으로 늘릴 수 있습니다(잡 자체는 멱등).

**1단계 — plist 절대경로 치환 (보호 폴더 밖 경로로)**

`scripts/com.itsangsang.morningbrief.weekly.plist` 를 열어 `/ABS/PATH/TO` 를 실제 절대경로로 교체합니다.
운영 배포본 경로(`~/AI-Morning-Brief-run/`)를 기준으로 치환하세요.

```bash
# python3 경로 확인
python3 -c "import sys; print(sys.executable)"
# 예: /Library/Frameworks/Python.framework/Versions/3.14/bin/python3

# 배포본 경로 확인
echo ~/AI-Morning-Brief-run
```

수정 항목:
- `ProgramArguments[0]` → 의존성이 설치된 python3 절대경로
- `ProgramArguments[1]` → `~/AI-Morning-Brief-run/main.py` 절대경로
- `WorkingDirectory` → `~/AI-Morning-Brief-run` 절대경로

**2단계 — LaunchAgents 등록**

```bash
cp scripts/com.itsangsang.morningbrief.weekly.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.itsangsang.morningbrief.weekly.plist
launchctl enable gui/$(id -u)/com.itsangsang.morningbrief.weekly
```

**3단계 — 등록 확인**

```bash
launchctl print gui/$(id -u)/com.itsangsang.morningbrief.weekly | grep -iE "state|program|working"
cat /tmp/morningbrief.weekly.out.log
cat /tmp/morningbrief.weekly.err.log
```

**등록 해제 / 재등록**

```bash
launchctl bootout gui/$(id -u)/com.itsangsang.morningbrief.weekly
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.itsangsang.morningbrief.weekly.plist
launchctl enable gui/$(id -u)/com.itsangsang.morningbrief.weekly
```

### 운영 배포본 동기화 (`~/AI-Morning-Brief-run`)

코드 갱신 후 운영 배포본에 재동기화합니다(일간과 동일 패턴):

```bash
rsync -a \
  --exclude='.env' --exclude='config/sources.yaml' \
  --exclude='data' --exclude='reports' \
  "/Users/lsh/Desktop/IT_make_some/projects/AI-Morning-Brief/05-개발/" \
  ~/AI-Morning-Brief-run/
```

- 주간 plist 경로는 `~/AI-Morning-Brief-run/main.py` 기준으로 설정합니다.
- 동기화 후 반드시 `plutil -lint ~/Library/LaunchAgents/com.itsangsang.morningbrief.weekly.plist` 로 plist 유효성을 확인하세요.

### 산출물

| 항목 | 위치 |
|------|------|
| 주간 전문(.md) | `reports/<ISO연도>/weekly/W##.md` |
| Discord 다이제스트 | 일간과 동일 웹훅 — 9블록 plain content, 2000자 이내 |

---

## 파일 구조

```
05-개발/
├── main.py                        # CLI 진입점
├── requirements.txt               # 의존성
├── README.md                      # 이 파일
├── config/
│   ├── config.yaml                # 일반 설정 (경로·파이프라인·Claude·Discord)
│   ├── sources.yaml               # 소스 목록 (운영자 작성, .gitignore 제외)
│   ├── sources.example.yaml       # 소스 예시 (복사 후 수정)
│   ├── operator_profile.md        # 운영자 프로필 (Claude 분석 렌즈)
│   └── .env.example               # 비밀값 예시 (복사 후 키 입력)
├── src/
│   ├── config_loader.py           # 설정·소스·비밀값·프로필 로더
│   ├── storage.py                 # SQLite 저장 계층
│   ├── collector.py               # RSS 수집기 + 소스 진단
│   ├── analyzer.py                # Claude 분석기 + fallback
│   ├── reporter.py                # Markdown 리포트 생성기
│   ├── notifier.py                # Discord Webhook 전송기
│   └── pipeline.py                # 파이프라인 오케스트레이션
├── data/
│   └── morning_brief.db           # SQLite DB (자동 생성, .gitignore 제외)
├── reports/
│   └── YYYY/
│       └── MM/
│           └── DD.md              # 일별 리포트 (자동 생성, .gitignore 제외)
├── tests/
│   ├── fixtures/
│   │   └── sample_articles.json   # 테스트용 샘플 기사 6건
│   └── test_pipeline.py           # pytest 통합 테스트 (QA-1~6)
└── scripts/
    └── com.itsangsang.morningbrief.plist  # launchd 설정 예시
```
