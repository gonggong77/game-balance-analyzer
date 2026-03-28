# game-balance-analyzer

> Drag and drop a game data CSV file to analyze it and validate balance metrics.

CSV 파일을 드래그 앤 드롭하면 Claude AI가 게임 밸런스를 자동으로 검증합니다.

## 시스템 흐름

```
사용자 (CSV + 규칙 입력)
        ↓
  웹앱 (Flask)
        ↓
Claude Code — 로컬 유니티 C# 스크립트 분석 (계산 공식 자동 추출)
        ↓
Claude API — 규칙 기반 밸런스 이상치 감지
        ↓
AI 대화 인터페이스 — 불확실 항목 질문 & 보완
        ↓
  최종 리포트
```

## 주요 특징

- **범용성** — 단테키우기뿐 아니라 어떤 게임의 CSV도 분석 가능
- **로컬 전용** — Claude Code가 로컬 유니티 프로젝트를 직접 읽어 계산 공식 자동 추출. 외부 전송 없음
- **대화형** — 불확실한 수치는 AI가 직접 질문해서 보완
- **규칙 기반** — 사용자가 검증 규칙을 직접 입력 가능

## 기술 스택

| 레이어 | 기술 |
|---|---|
| 웹앱 백엔드 | Python + Flask |
| 웹앱 프론트엔드 | Vanilla JS + Tailwind CDN |
| AI 코드 분석 | Claude Code (로컬) |
| AI 밸런스 분석 | Anthropic API (claude-sonnet-4) |
| 배포 | Render |

## 설치 및 실행

```bash
# 1. 레포 클론
git clone https://github.com/gonggong77/game-balance-analyzer.git
cd game-balance-analyzer

# 2. 가상환경 생성 및 활성화
python -m venv venv
# Windows
venv\Scripts\activate
# Mac/Linux
source venv/bin/activate

# 3. 패키지 설치
pip install -r requirements.txt

# 4. 환경변수 설정
cp .env.example .env
# .env 파일에 ANTHROPIC_API_KEY 입력

# 5. 실행
python app.py
```

## 프로젝트 구조

```
game-balance-analyzer/
├── data/                        # 샘플 CSV 데이터
│   ├── skills.csv               # 스킬 기본 수치
│   ├── skillLevels.csv          # 스킬 강화 수치
│   ├── stats.csv                # 골드 성장
│   ├── levels.csv               # 레벨 성장
│   ├── accessory.csv            # 장신구
│   ├── accessory_level_range.csv
│   ├── equipment.csv            # 장비 6부위
│   ├── relic.csv                # 유물 12종
│   └── pet.csv                  # 펫 4종
├── app.py                       # Flask 서버 (Phase 3)
├── balance_checker.py           # 핵심 분석 엔진 (Phase 2)
├── requirements.txt
├── .env.example
└── README.md
```

## 개발 로드맵

- [x] Phase 1 — CSV 샘플 데이터 설계 & 환경 세팅
- [ ] Phase 2 — Claude API 연동 + 분석 로직
- [ ] Phase 3 — Flask 웹앱 + CSV 드래그 앤 드롭 UI
- [ ] Phase 4 — 배포 + 포트폴리오 정리
