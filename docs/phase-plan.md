# Phase plan — kakao-import-local

<!-- [변경사유]: v1.3 — development-plan과 동기. Phase 3.5 운영 안정화 최우선, Phase 4 보류 -->

상세 본문: [development-plan.md](./development-plan.md) **v1.3**  
운영 안정화 전문: [phase3-ops-stabilization.md](./phase3-ops-stabilization.md)

| Phase | 우선 | 한 줄 | 상태 |
|-------|-----:|------|------|
| **0** | — | 계약·golden·개인정보·SQLite·payload | ✅ |
| **0.5** | 낮음 | signature Python 조사만 | Phase 4 직전 재확인 |
| **1** | — | parser + **파일명 시각 matcher** + 이력 + SHA + 리포트 | ✅ |
| **1.5** | 보류 | watcher·자동 실행 | **3.5·E2E 후** |
| **2** | — | 텍스트 merge (safe/balanced/auto) | ✅ |
| **3** | — | Import·서버 exact·OCR/GPT | ✅ 기능 (운영 이슈→3.5) |
| **3.5** | **지금** | caption replay · empty gate · file_missing · UTF-8 | **최우선** |
| **3.6** | 대기 | SHA 재사용 · similar_hold 자산화 · 거부 목록 | **v0.2** — [ingest-dedup-reject-plan.md](./ingest-dedup-reject-plan.md). 진행 지시 후 착수. 거부 관리 화면은 이후 |
| **4** | 이후 | similar 그룹 UI (합침/분리) | **3.5 전 착수 금지** |
| **5** | 마지막 | 제한 자동 승인 | 이후 |

## 입력 (확정)

```text
input/raw/chats/*.txt   +   input/raw/photos/*
```

매칭 주 경로: `KakaoTalk_YYYYMMDD_HHMMSSmmm` → datetime ↔ 메시지 절대시각  
(문자열로 txt에 파일명이 적히지는 않음)

Phase 1 완료 = [golden-esencia-20260724-0050.md](./golden-esencia-20260724-0050.md) 자동 통과 포함.

## 지금 하지 않는 것

- Phase 4 로컬 Similar (서버 Similar hold가 본경로)
- watcher / 자동 승인 (데이터 신뢰성 확보 전)
- PC 카톡 UI 수집은 이 레포가 아님 — [kakao-pc-collect-plan.md](./kakao-pc-collect-plan.md)
- Exact/similar/신규 OCR happy path 변경 (3.5는 안정화만)

## 병렬: 포스터 분류 (Phase 번호를 쓰지 않음)

3.5·4를 막지 않음. 계약 [poster-classifier-plan.md](./poster-classifier-plan.md) · 개발 [poster-classifier-dev.md](./poster-classifier-dev.md) (C0~C3).
