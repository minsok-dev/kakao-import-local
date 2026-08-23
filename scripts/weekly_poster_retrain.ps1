# [변경사유]: B7 — 주간 poster-retrain-from-review Task Scheduler 예시 (opt-in)
# 사용: 작업 스케줄러에서 주 1회(예: 일요일 10:00) 실행.
# 야간 collect(upload) 체인과 시각이 겹치지 않게 잡을 것.
# activate 는 기본 끔 — 게이트 통과 후 수동 또는 -Activate 스위치.

param(
    [string]$ImportRoot = "",
    [switch]$Activate,
    [switch]$ActivateForce
)

$ErrorActionPreference = "Stop"

if (-not $ImportRoot) {
    $ImportRoot = Split-Path -Parent $PSScriptRoot
}

Set-Location $ImportRoot
$venvActivate = Join-Path $ImportRoot ".venv\Scripts\Activate.ps1"
if (Test-Path $venvActivate) {
    . $venvActivate
}

$argsList = @("poster-retrain-from-review")
if ($Activate) {
    $argsList += "--activate"
}
if ($ActivateForce) {
    $argsList += "--activate-force"
}

Write-Host "[weekly-poster-retrain] root=$ImportRoot args=$($argsList -join ' ')"
& kakao-import @argsList
exit $LASTEXITCODE
