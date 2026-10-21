# Oracle Link - keeps the reverse tunnel open (started by the OracleLink task).
$dir = Split-Path -Parent $PSCommandPath
$cfg = Get-Content (Join-Path $dir 'config.json') -Raw | ConvertFrom-Json
$log = Join-Path $dir 'link.log'

function Log($message) {
    $line = '{0:yyyy-MM-dd HH:mm:ss}  {1}' -f (Get-Date), $message
    Add-Content -Path $log -Value $line
    if ((Get-Item $log).Length -gt 1MB) { Move-Item $log "$log.old" -Force }
}

$ssh = $null
foreach ($candidate in @((Join-Path $dir 'OpenSSH\ssh.exe'), "$env:WINDIR\System32\OpenSSH\ssh.exe", "$env:WINDIR\Sysnative\OpenSSH\ssh.exe")) {
    if (Test-Path $candidate) { $ssh = $candidate; break }
}
if (-not $ssh) {
    $cmd = Get-Command ssh.exe -ErrorAction SilentlyContinue
    if ($cmd) { $ssh = $cmd.Source }
}
if (-not $ssh) {
    Log 'ssh.exe not found - run install.ps1 again.'
    exit 1
}

$forward = '127.0.0.1:{0}:{1}:{2}' -f $cfg.remote_port, $cfg.oracle_host, $cfg.oracle_port
$sshArgs = @(
    '-N', '-T',
    '-i', (Join-Path $dir 'id_ed25519'),
    '-p', [string]$cfg.ssh_port,
    '-o', 'BatchMode=yes',
    '-o', 'IdentitiesOnly=yes',
    '-o', 'ExitOnForwardFailure=yes',
    '-o', 'ServerAliveInterval=30',
    '-o', 'ServerAliveCountMax=3',
    '-o', 'StrictHostKeyChecking=yes',
    '-o', ('UserKnownHostsFile=' + (Join-Path $dir 'known_hosts')),
    '-R', $forward,
    ('{0}@{1}' -f $cfg.user, $cfg.server)
)

while ($true) {
    Log ("connecting to {0}:{1} (Oracle {2}:{3})" -f $cfg.server, $cfg.ssh_port, $cfg.oracle_host, $cfg.oracle_port)
    & $ssh @sshArgs 2>&1 | ForEach-Object { Log $_ }
    Log "disconnected (exit $LASTEXITCODE) - retrying in 10 seconds"
    Start-Sleep -Seconds 10
}
