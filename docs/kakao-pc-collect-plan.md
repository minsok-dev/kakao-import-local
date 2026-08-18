# 카카오톡 PC 수집 도구 — 계획 (kakao-import와 분리)

<!-- [변경사유]: 2026-08-17 — PC UI 준자동 수집 계약. UIA 한계·서랍 50칸·고정 다운로드 폴더 반영 -->
<!-- [변경사유]: 2026-08-17 — 대화 txt: 검색→Enter→Ctrl+S→저장 실측 확정 -->
<!-- [변경사유]: 2026-08-17 — 하이브리드 확정. 서랍 가상스크롤·다운로드 좌표 3곳·워터마크 중단 규칙 반영 -->

| 항목 | 내용 |
|------|------|
| 기준일 | 2026-08-17 |
| 상태 | **계약·실측 확정 · 골격 구현** (`kakao-pc-collect`) |
| 구현 위치 | **`F:/site_kdance/TEST_web/kakao-pc-collect`** (이 레포에 UI 자동화 넣지 않음) |
| 대상 방 | 단체방 **약 6개** (허용 목록) |
| 산출물 | `input/raw/<room_id>/chats/*.txt` + `input/raw/<room_id>/photos/KakaoTalk_*` (원본 파일명·형식 유지) |
| 후속 | 수집 종료 시 `kakao-import run` + `similar-detect` **명시 호출** (폴더 watcher 아님) |

관련: [development-plan.md](./development-plan.md) · [phase0-contracts.md](./phase0-contracts.md) · [privacy-retention.md](./privacy-retention.md) · [similar-group-decisions.md](./similar-group-decisions.md) · [poster-classifier-plan.md](./poster-classifier-plan.md)

---

## 1. 배경 · 목표

여러 카카오톡 단체방에서 **이미지 + 대화 내용**을 모아 danceinfo에 포스터·홍보문구로 올리는 업무를 자동화한다.

| 구간 | 담당 |
|------|------|
| PC에서 txt·사진 꺼내기 | **도구 A (본 문서)** — 별도 구현 |
| 매칭·SHA·similar·업로드 | 기존 **`kakao-import` CLI** (이미 있음) |

`kakao-import` 운영 순서 (변경 없음):

```text
run → similar-detect → similar-review(사람) → upload --dry-run → upload --no-dry-run
```

<!-- [변경사유]: 잡사진 제외는 수집기가 아니라 import 분류기. 계약만 있음 -->
포스터 vs 일상 사진 분류는 수집 단계가 아니다. 계약: [poster-classifier-plan.md](./poster-classifier-plan.md) · 개발: [poster-classifier-dev.md](./poster-classifier-dev.md) (미구현).

입력은 방별 `input/raw/<room_id>/chats` + `photos`이다. 구 `chats/`+`photos/`는 `_legacy` 호환.  
카톡이 만드는 **원본 파일명·형식**을 유지해야 한다 (리네임·재인코딩 금지).

---

## 2. 방향 (하이브리드)

처음에는 무인 새벽 스케줄 + **전체 좌표** 자동화를 검토했으나, **하이브리드**로 전환했다.

| 원칙 | 내용 |
|------|------|
| 공식 메뉴만 | 카카오 서버 크롤 안 함, 로컬 DB/캐시 직접 안 읽음 |
| 사람 세션 | 카톡이 **켜진·로그인된** PC에서 실행. 완전 무인 새벽 봇 아님 |
| UIA 우선 | 표준 컨트롤은 pywinauto / 키보드 |
| 좌표는 최소 | UIA로 안 잡히는 **3곳만** 창 상대 좌표(또는 이미지 매칭) |
| import 분리 | A는 파일만 모음. similar 판정·upload는 기존 CLI + 사람 |

---

## 3. 하지 않는 것

- `%LocalAppData%\Kakao\KakaoTalk` DB·캐시 직접 읽기
- 웹 카카오 / 비공식 프로토콜
- 로그인·QR 자동화
- 채팅 목록·서랍 칸을 UIA Name으로 찾아 클릭
- 사진 **한 장씩** 저장
- 카톡 **칸 50** 제한 우회
- `kakao-import`가 `Documents\카카오톡 받은 파일`을 직접 스캔
- similar `same_content` 자동 판정
- 수집 직후 서버 upload까지 무인 연결
- `input/raw` 원본 삭제·리네임
- 다운로드 트리거를 Enter / Ctrl+S / Ctrl+D / Shift+F10 / 메뉴키로 대체하려 함 → **전부 실패 확인**, 좌표 클릭으로 확정

---

## 4. 전체 흐름

```text
[사람] 카카오톡 PC 로그인 (실행 시각에 켜 둘 것)
        │
        ▼
[도구 A · 별도] 허용 방 6개 등
  ① 검색 Edit set_text → Enter → 방 열림          (좌표 불필요)
  ② Ctrl+S → 표준 저장창 → chats 경로 → 저장     (좌표 불필요)
  ③ ☰ 좌표 → 서랍 → 사진/동영상
  ④ 첫 사진 앵커 좌표 → (가상스크롤) → Shift 선택 ≤50칸
  ⑤ 다운로드 버튼 좌표/이미지매칭 클릭
        │  사진은 카톡 고정 폴더
        ▼
D:\Users\msgu\Documents\카카오톡 받은 파일
        │  신규 KakaoTalk_*.png/jpg 만 이름 유지 복사
        ▼
kakao-import-local/input/raw/<room_id>/chats
kakao-import-local/input/raw/<room_id>/photos
        │  A가 “이번 회차 끝”일 때 호출
        ▼
kakao-import run
kakao-import similar-detect
        │
        ▼
[사람] similar-review
        │
        ▼
kakao-import upload --dry-run   (선택)
kakao-import upload --no-dry-run
```

경계에서 이미 받은 장이 다시 들어와도 **exact(alldup) / similar-detect**가 걸러 준다.

---

## 5. UI 구조 진단 (Accessibility Insights)

| 요소 | 결과 | 자동화 |
|------|------|--------|
| 메인 검색창 | ✅ 표준 Edit | `set_text` |
| 검색 결과 목록 | ❌ 이름 없는 Pane (캔버스) | 행 클릭 불가. Enter로 첫 결과 열기 |
| 채팅방 서랍 사진 그리드 | ❌ Pane | 개별 칸 UIA 접근 불가 |
| 방 메뉴(☰) | ❌ Pane | **좌표** |
| 드롭다운 메뉴 항목 | ❌ Pane | 좌표 또는 키보드(서랍 경로) |
| 「다른 이름으로 저장」 | ✅ 표준 Win32 Save | UIA로 경로·저장 |

부모 창 이름 `_0x….` 는 HWND → 셀렉터로 쓰지 않음.

---

## 6. 검증된 자동화 흐름

### 6.1 방 열기 (좌표 불필요)

```text
검색창에 방이름 입력 (pywinauto set_text)
  → Enter
  → 첫 검색결과가 기본 포커스 → 방 열림
```

- 검색어는 **고유**해야 한다 (허용 목록으로 보장).
- 행 클릭·`↓` 불필요.

### 6.2 대화 내보내기 (좌표 불필요)

```text
방에 포커스
  → Ctrl+S          (「다른 이름으로 저장」)
  → 파일명: 카톡이 KakaoTalk_시각_group.txt 자동 생성
  → 경로만 지정 후 저장
```

- 실측 마지막 폴더:  
  `F:\site_kdance\TEST_web\kakao-import-local\input\raw\chats`  
  → 한 번 맞춰 두면 이후 **저장 클릭만**으로 충분.
- 내보내기 시각 토큰은 **사진 매칭에 쓰지 않음**.
- 방당 회차당 **1회**. txt 자르기 없음.

### 6.3 서랍 열기 (좌표 1)

- 방 메뉴(☰)가 Pane → **방 창 클라이언트 상대 좌표**로 클릭.
- 상대 위치는 창 크기가 같으면 고정적.

### 6.4 사진 선택 (좌표 1 + 키보드, 검증 완료)

카톡 **칸 50** 제한. 앨범은 칸 1개 = 파일 여러 장(`_01`…) → 50칸에 파일 100+ 가능.

**가상 스크롤** 때문에 처음 로드된 약 30칸 이후는 바로 Shift 선택이 안 된다. 확정 순서:

```text
1) 첫 사진 위치 좌표 클릭 (앵커, 1회)
2) Shift 없이 방향키로 끝까지 이동 → 스크롤 로딩
3) 포커스를 첫 사진으로 되돌림
4) Shift+방향키로 최대 50칸 재선택
```

### 6.5 다운로드 트리거 (좌표/이미지매칭 1)

시도 후 **실패** 확인:

| 키 | 결과 |
|----|------|
| Enter | 안 됨 |
| Ctrl+S | 안 됨 (대화 저장용) |
| Ctrl+D | 안 됨 |
| Shift+F10 / 메뉴키 | 안 됨 |
| Shift+PageDown | 안 됨 |

우클릭 저장 팝업은 뜨지만 Accessibility Insights에 **안 잡혀** 키보드 재현 불가.  
→ **다운로드 버튼은 좌표 또는 이미지 매칭 클릭**으로 최종 결정.

다운로드 후 파일은 항상:

`D:\Users\msgu\Documents\카카오톡 받은 파일`  
→ 신규 `KakaoTalk_*.png/jpg`만 `input/raw/<room_id>/photos`로 **이름 유지 복사**. `.mp4` 제외.

파일명 예:

```text
KakaoTalk_20260815_234947259.png
KakaoTalk_20260815_234947259_01.png
```

Explorer 수정 시각은 다운로드 시각 → 따라잡기 기준 아님. **파일명 스템 시각**만 사용.

### 6.6 배치 끝 감지

“파일 50개”가 완료 신호가 아님.  
다운로드 클릭 이후 `KakaoTalk_` 파일이 **수초간 더 생기지 않으면** 그 배치 끝 (앨범 전개 대기).

---

## 7. 좌표 의존 지점 (딱 3곳)

| # | 지점 | 방식 |
|---|------|------|
| 1 | 서랍 열기 (☰) | 방 창 **상대 좌표** |
| 2 | 사진 선택 앵커 (서랍 첫 칸) | 서랍 창 **상대 좌표** |
| 3 | 다운로드 버튼 | **상대 좌표 또는 이미지 매칭** |

그 외(검색·Enter·Ctrl+S·저장창·방향키 선택)는 UIA/키보드.

좌표는 DPI·창 크기·카톡 업데이트에 깨질 수 있으므로 **캘리브레이션 설정**(방 창/서랍 창 기준 offset)으로 두고, 실패 시 해당 회차 중단.

---

## 8. 물량 · 따라잡기 (고정 개수 금지)

하루 이미지 양이 커서 “한 번에 N장” 고정은 맞지 않는다.

```text
칸 50개 배치 반복
  → Documents → photos 복사
  → 이번 배치 파일명 스템 시각 확인
  → 마지막 실행 워터마크(또는 photos 최신 스템)보다
     오래된 스템이 나오면 중단
```

| 이번 배치 | 다음 |
|-----------|------|
| 스템이 전부 워터마크 **이후(신규)** | 칸 50 한 번 더 |
| **워터마크보다 오래된** 스템이 섞이거나 나오기 시작 | 신규분만 복사하고 **이 방 중단** |
| 전부 이미 `photos`에 있음 | 이 방 그만 |

경계 중복은 기존 **exact / similar-detect**가 걸러 준다.  
목표 빈도: **하루 2회** (시각 운영 미정). 2회차는 보통 첫 배치에서 바로 중단되는 경우가 많다.

워터마크는 도구 A가 `data/` 등에 **방별 마지막 처리 스템**을 저장해도 되고, `photos` 최신 스템을 써도 된다.

---

## 9. 허용 방 (초안)

검색 → Enter 시 **첫 결과 = 목표 방**이어야 한다.

| 검색어 | txt 예 | 비고 |
|--------|--------|------|
| `강남 라틴클럽` | `KakaoTalk_20260817_0324_06_860_group.txt` | 운영 확인 |
| (나머지 ~5방) | | 검색 유일성 확인 후 기입 |

---

## 10. 자동화 수위 · 스케줄

| 구간 | 자동? |
|------|--------|
| 방 검색·열기 | ✅ UIA + Enter |
| 대화 txt | ✅ Ctrl+S + Win32 저장창 |
| 서랍·50칸·다운로드 | ✅ 키보드 + **좌표 3곳** (실패 시 중단) |
| Documents → photos | ✅ |
| `run` / `similar-detect` | 수집 성공 후 호출 |
| similar-review | **사람** |
| upload | 리뷰 후 |

Phase 1.5 폴더 watcher는 이후. 지금은 A 종료 시 import 호출.

---

## 11. 구현 체크리스트

**확정됨**

- [x] 검색 → Enter 방 열기
- [x] Ctrl+S 대화 내보내기 + chats 마지막 폴더
- [x] 서랍 사진 UIA 불가 → 좌표 3곳
- [x] 가상 스크롤: 끝 이동 후 첫 칸 복귀 → Shift 선택
- [x] 다운로드 키보드 불가 → 좌표/이미지매칭
- [x] Documents 고정 경로 + photos 복사
- [x] 워터마크 기반 배치 중단 (고정 개수 아님)

**구현 시 채울 것**

- [ ] 허용 방 6개 검색어 목록 (`kakao-pc-collect/config/rooms.yaml` — 현재 1방 초안)
- [x] 좌표 캘리브레이션 저장 형식 — `config/coords.yaml` + `kakao-pc-collect calibrate`
- [ ] 하루 2회 시각
- [x] 도구 A 위치·CLI — `F:/site_kdance/TEST_web/kakao-pc-collect`, CLI `kakao-pc-collect`

스파이크: 방 1개로 ①~⑤ + 복사 + `kakao-import run` 한 바퀴 (좌표 실측 후).
