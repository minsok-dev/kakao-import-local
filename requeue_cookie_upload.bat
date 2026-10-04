@echo off
REM [변경사유]: 쿠키를 새 슈퍼관리자 세션으로 바꾼 뒤에만 403 건을 다시 업로드
cd /d "%~dp0"
echo.
echo KAKAO_IMPORT_SESSION_COOKIE 를 슈퍼관리자 로그인 쿠키로 바꾼 뒤에 실행하세요.
echo 쿠키가 그대로면 다시 403 이 나고 업로드가 멈춥니다.
echo.
if not exist ".venv\Scripts\python.exe" (
  echo .venv\Scripts\python.exe 가 없습니다.
  exit /b 1
)
.venv\Scripts\python.exe scripts\requeue_insufficient_permission.py
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe -m kakao_import upload --no-dry-run
exit /b %ERRORLEVEL%
