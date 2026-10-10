# Installs (or with -Uninstall removes) the gatekeeper behind pinchtab-windows:
# its Python dependencies, and a logon task that runs it with pythonw from this
# checkout. A logon task, not a service: a service runs outside Louie's desktop
# session and could show neither the approval dialog nor the tray icon.
#
# cloudflared is separate: see README.md.
param([switch]$Uninstall)
$ErrorActionPreference = 'Stop'
$TaskName = 'pinchtab-remote gatekeeper'

if ($Uninstall) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Output "Removed '$TaskName'. State and audit log remain in $env:LOCALAPPDATA\pinchtab-remote."
    return
}

$pythonw = (Get-Command pythonw -ErrorAction Stop).Source
$script = Join-Path $PSScriptRoot 'gatekeeper.py'
python -m pip install --user --quiet aiohttp pystray pillow
if ($LASTEXITCODE -ne 0) { throw 'pip install failed' }

$action = New-ScheduledTaskAction -Execute $pythonw -Argument "`"$script`"" -WorkingDirectory $PSScriptRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal `
    -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName
Write-Output "Registered and started '$TaskName' ($pythonw $script)."
Write-Output "Log: $env:LOCALAPPDATA\pinchtab-remote\gatekeeper.log"
