param(
    [Parameter(Mandatory = $true)]
    [ValidateRange(1, 65535)]
    [int]$Port,

    [Parameter(Mandatory = $true)]
    [string]$ProjectRoot
)

$ErrorActionPreference = "Stop"

$resolvedRoot = [System.IO.Path]::GetFullPath($ProjectRoot).TrimEnd("\")
$expectedPython = [System.IO.Path]::GetFullPath(
    (Join-Path $resolvedRoot ".venv\Scripts\python.exe")
)
$pythonPattern = '(?i)^\s*"?' + [regex]::Escape($expectedPython) + '"?(?:\s|$)'
$serverPattern = '(?i)(^|[\s"\\])server\.py(?:[\s"]|$)'

$listenerIds = @(
    Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique
)

if ($listenerIds.Count -eq 0) {
    exit 0
}

$foreignProcesses = @()
foreach ($ownerId in $listenerIds) {
    try {
        $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $ownerId"
    }
    catch {
        $foreignProcesses += "PID $ownerId（无法读取进程信息）"
        continue
    }
    if ($null -eq $processInfo) {
        continue
    }

    $samePython = $processInfo.CommandLine -match $pythonPattern
    $isProjectServer = $processInfo.CommandLine -match $serverPattern
    if (-not ($samePython -and $isProjectServer)) {
        $foreignProcesses += "PID $ownerId ($($processInfo.Name))"
        continue
    }

    Write-Host "[启动前清理] 正在强制停止旧服务 PID $ownerId（端口 $Port）..."
    Stop-Process -Id $ownerId -Force
}

if ($foreignProcesses.Count -gt 0) {
    $details = $foreignProcesses -join "、"
    [Console]::Error.WriteLine(
        "端口 $Port 被非当前项目进程占用：$details。为避免误杀，启动已取消。"
    )
    exit 2
}

Start-Sleep -Milliseconds 200
$remainingListenerIds = @(
    Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique
)
if ($remainingListenerIds.Count -gt 0) {
    $details = $remainingListenerIds -join "、"
    [Console]::Error.WriteLine(
        "旧服务未完全退出，端口 $Port 仍由 PID $details 监听。"
    )
    exit 3
}

exit 0
