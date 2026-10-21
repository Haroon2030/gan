# Oracle Link - installer (run once on the Oracle database machine).
# Copies the package to C:\OracleLink and registers a startup task that keeps
# an outbound SSH reverse tunnel open to the dashboard server.
$ErrorActionPreference = 'Stop'

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Start-Process powershell.exe -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    exit
}

$src = Split-Path -Parent $PSCommandPath
$dest = 'C:\OracleLink'
$taskName = 'OracleLink'

function Done($code) {
    Write-Host ''
    Read-Host 'Press Enter to close'
    exit $code
}

try {
    foreach ($f in 'id_ed25519', 'known_hosts', 'config.json', 'tunnel.ps1') {
        if (-not (Test-Path (Join-Path $src $f))) {
            Write-Host "Missing file: $f" -ForegroundColor Red
            Write-Host 'Download a fresh package from the dashboard: Oracle connection screen > Generate link file.'
            Done 1
        }
    }

    Write-Host '[1/5] Checking OpenSSH client...'
    $ssh = Get-Command ssh.exe -ErrorAction SilentlyContinue
    if (-not $ssh -and -not (Test-Path "$env:WINDIR\System32\OpenSSH\ssh.exe") -and -not (Test-Path (Join-Path $src 'OpenSSH\ssh.exe'))) {
        try {
            Add-WindowsCapability -Online -Name 'OpenSSH.Client~~~~0.0.1.0' | Out-Null
            Write-Host '      OpenSSH client installed.'
        } catch {
            Write-Host '      Could not install OpenSSH automatically (older Windows).' -ForegroundColor Yellow
            Write-Host '      Download OpenSSH-Win64.zip from https://github.com/PowerShell/Win32-OpenSSH/releases'
            Write-Host "      extract it as '$src\OpenSSH' (ssh.exe inside), then run this installer again."
            Done 1
        }
    }

    Write-Host "[2/5] Copying files to $dest..."
    if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    }
    Get-CimInstance Win32_Process -Filter "Name='ssh.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like '*OracleLink*' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    New-Item -ItemType Directory -Force -Path $dest | Out-Null
    if ((Resolve-Path $src).Path.TrimEnd('\') -ne $dest) {
        Copy-Item -Path (Join-Path $src '*') -Destination $dest -Recurse -Force
    }

    Write-Host '[3/5] Securing the key file...'
    # SSH refuses keys readable by other users. SIDs work on any Windows language.
    $key = Join-Path $dest 'id_ed25519'
    & icacls.exe $key /inheritance:r /grant:r '*S-1-5-18:F' '*S-1-5-32-544:F' | Out-Null

    Write-Host '[4/5] Checking local Oracle listener...'
    $cfg = Get-Content (Join-Path $dest 'config.json') -Raw | ConvertFrom-Json
    $probe = New-Object Net.Sockets.TcpClient
    try {
        $probe.Connect($cfg.oracle_host, [int]$cfg.oracle_port)
        Write-Host "      Oracle is listening on $($cfg.oracle_host):$($cfg.oracle_port)."
    } catch {
        Write-Host "      WARNING: nothing answers on $($cfg.oracle_host):$($cfg.oracle_port). Edit config.json if Oracle uses another port." -ForegroundColor Yellow
    } finally {
        $probe.Close()
    }

    Write-Host '[5/5] Registering startup task...'
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$dest\tunnel.ps1`""
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $runAs = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
        -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $runAs -Settings $settings -Force | Out-Null
    Start-ScheduledTask -TaskName $taskName

    Write-Host ''
    Write-Host 'Waiting for the first connection...'
    Start-Sleep -Seconds 8
    $log = Join-Path $dest 'link.log'
    if (Test-Path $log) { Get-Content $log -Tail 6 }
    Write-Host ''
    Write-Host "Installed. The link starts automatically with Windows. Log: $log" -ForegroundColor Green
    Write-Host 'Check the status on the dashboard: Oracle connection screen.'
    Done 0
} catch {
    Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
    Done 1
}
