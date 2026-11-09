# 부서 AI 서버 하나를 잠깐 멈췄다가 풀어서 "다시 시도·시간 초과" 장면을 실제로 만든다 (Windows 로컬 전용).
# 에이전트 평가 화면의 "확인 필요"는 총괄이 부서를 한 번 부를 때 30초 안에 답을 못 받아 다시 시도해야 나온다.
# 평소 로컬 장애는 모두 몇 초 안에 끝나서 이 장면이 안 나오므로, 시연·시험 때 이 스크립트로 만든다.
#
# 사용 (run_all.sh 로 서버를 띄운 뒤, 장애를 넣고 이벤트가 열리기 직전에 실행):
#   bash backend/scripts/shop_fault.sh failure
#   powershell -File backend/scripts/slow_agent.ps1 -Port 8106 -DelaySeconds 20 -HoldSeconds 38
#
# -Port          멈출 서버 포트 (데이터분석 8106, 마케팅 8103(coupon)·고객지원 8102~8112 등 run_all.sh 표 참고)
# -DelaySeconds  명령을 받고 멈추기까지 기다릴 시간. 이벤트가 열려 그 부서를 부르기 직전에 멈추도록 맞춘다
# -HoldSeconds   멈춰 둘 시간. 총괄의 한 번 호출 마감(30초)보다 길어야 다시 시도가 난다
#
# 서버 프로세스를 지우거나 고치지 않는다. 멈췄다가 그대로 이어 간다(중간에 끊겨도 마지막에 반드시 푼다).
param(
  [Parameter(Mandatory = $true)][int]$Port,
  [int]$DelaySeconds = 0,
  [int]$HoldSeconds = 38
)
$ErrorActionPreference = "Stop"

Add-Type -Namespace Ax -Name Proc -MemberDefinition @'
[System.Runtime.InteropServices.DllImport("ntdll.dll")] public static extern int NtSuspendProcess(System.IntPtr h);
[System.Runtime.InteropServices.DllImport("ntdll.dll")] public static extern int NtResumeProcess(System.IntPtr h);
'@

$listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $listener) { Write-Error "포트 $Port 에서 듣는 서버가 없습니다. run_all.sh 로 먼저 띄우세요."; exit 1 }
$proc = Get-Process -Id $listener.OwningProcess
if ($DelaySeconds -gt 0) { Write-Host "$DelaySeconds초 뒤에 $($proc.ProcessName)(pid $($proc.Id), 포트 $Port)를 멈춥니다"; Start-Sleep -Seconds $DelaySeconds }
Write-Host "멈춤: $($proc.ProcessName) pid $($proc.Id) 포트 $Port, $HoldSeconds초"
[void][Ax.Proc]::NtSuspendProcess($proc.Handle)
try { Start-Sleep -Seconds $HoldSeconds }
finally {
  [void][Ax.Proc]::NtResumeProcess($proc.Handle)
  Write-Host "풀었습니다: pid $($proc.Id)"
}
