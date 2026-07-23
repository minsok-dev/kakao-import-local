# Phase plan — kakao-import-local

<!-- [변경사유]: v1.2 — development-plan과 동기, 시각 matcher·입력 레이아웃 명시 -->

상세 본문: [development-plan.md](./development-plan.md) **v1.2**

| Phase | 우선 | 한 줄 |
|-------|-----:|------|
| **0** | 지금 | 계약·golden·개인정보·SQLite·payload |
| **0.5** | 지금 | signature Python 조사만 |
| **1** | 최우선 | parser + **파일명 시각 matcher** + 이력 + SHA + 리포트 |
| **1.5** | 이후 | watcher·자동 실행 |
| **2** | 다음 | 텍스트 merge (safe/balanced/auto) |
| **3** | 다음 | Import·서버 exact·OCR/GPT |
| **4** | 이후 | similar 그룹 UI (합침/분리) |
| **5** | 마지막 | 제한 자동 승인 |

## 입력 (확정)

```text
input/raw/chats/*.txt   +   input/raw/photos/*
```

매칭 주 경로: `KakaoTalk_YYYYMMDD_HHMMSSmmm` → datetime ↔ 메시지 절대시각  
(문자열로 txt에 파일명이 적히지는 않음)

Phase 1 완료 = [golden-esencia-20260724-0050.md](./golden-esencia-20260724-0050.md) 자동 통과 포함.
