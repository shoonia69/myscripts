$started += Start-Process "C:\Users\mailo\OpenVPN\config\start-vpn.bat"

$started += Start-Process "C:\Program Files\Google\Chrome\Application\chrome.exe" -PassThru

$started += Start-Process "C:\Users\mailo\AppData\Local\Programs\mattermost-desktop\Mattermost.exe" -PassThru
$started += Start-Process "C:\Program Files\Talks\Talks.exe" -PassThru
$started += Start-Process "C:\Program Files\Microsoft Office\root\Office16\OUTLOOK.EXE" -PassThru
$started += Start-Process "C:\Users\mailo\AppData\Local\Programs\TeamSpeak\TeamSpeak.exe" -PassThru

$started += Start-Process "C:\Users\mailo\AppData\Local\Programs\OpenWhispr\OpenWhispr.exe" -PassThru


 

