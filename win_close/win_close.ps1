# Список процессов для завершения
$processes = @(
    "OUTLOOK",
    "teamspeak",          # TeamSpeak
    "Talks",
    "mattermost",
    "Openwhispr",
    "vmware-view",        # VMware Horizon Client
    "Zoom"
)

foreach ($process in $processes) {
    Get-Process -Name $process -ErrorAction SilentlyContinue | Stop-Process -Force
}

Write-Host "Все указанные процессы завершены."