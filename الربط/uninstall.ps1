# Oracle Link - removes the startup task, stops the tunnel and deletes C:\OracleLink.
$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Start-Process powershell.exe -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    exit
}

$taskName = 'OracleLink'
$dest = 'C:\OracleLink'

if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { ($_.Name -eq 'ssh.exe' -or $_.Name -eq 'powershell.exe') -and $_.CommandLine -like '*OracleLink*' -and $_.ProcessId -ne $PID } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
if ((Split-Path -Parent $PSCommandPath) -ne $dest -and (Test-Path $dest)) {
    Remove-Item $dest -Recurse -Force
}

Write-Host 'Oracle Link removed.' -ForegroundColor Green
Read-Host 'Press Enter to close'
