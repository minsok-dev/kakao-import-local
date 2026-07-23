# Phase 0.5 — Python ↔ danceinfo_image_signature 호환성

<!-- [변경사유]: Phase 4 전에 feasibility만 확인 — 유사 기능은 구현하지 않음 -->

## 목적

로컬에서 similar를 쓸 수 있는지 **지금** 짧게 확인하고, 연동 방식을 하나로 고정한다.  
Phase 4에서 “쓸 수 없다”를 발견해 구조를 뒤집지 않기 위함.

## 조사만 하는 것 / 하지 않는 것

| 함 | 안 함 |
|----|------|
| pip editable 설치 | similar 매칭 구현 |
| golden vector 해시 일치 | UI |
| 결과 문서에 방식 고정 | 임계값 튜닝 |

## 후보 결과 (반드시 하나 선택)

1. **Python binding 직접 사용** — `pip install -e ../backend/packages/danceinfo_image_signature`  
2. **공용 CLI/subprocess**로 호출  
3. **로컬은 SHA만**, signature는 **서버에서 계산**  
4. 동일 규격을 로컬 Python으로 재구현 + **cross-language fixture** 검증  

## 사전 메모

- `F:/site_kdance/TEST_web/backend/packages/danceinfo_image_signature` 는 이미 **Python 패키지**로 존재함.  
- 따라서 1번이 유력하나, **이 PC의 Python 3.13 + 의존성 + golden 테스트 통과**를 실행해 확정해야 함.

## 체크리스트

- [ ] venv에서 editable 설치 성공  
- [ ] `pytest` (패키지 테스트 / golden_vectors) 통과  
- [ ] kakao-import-local에서 `import danceinfo_image_signature` 스모크  
- [ ] 결과: 위 1~4 중 **하나**를 본 문서 §결과 에 기록  

## 결과 (조사 후 기입)

| 항목 | 값 |
|------|-----|
| 날짜 | (미실시) |
| 선택 | (미정) |
| Python | |
| 비고 | |
