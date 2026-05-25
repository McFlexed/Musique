@"
@echo off
:loop
"C:\Users\Maywen\AppData\Local\Programs\Python\Python312\python.exe" "C:\Users\Maywen\Documents\RudeusFaitdelaMusique\rudeus.py"
timeout /t 5
goto loop
"@ | Out-File -FilePath "$HOME\Documents\RudeusFaitdelaMusique\start.bat" -Encoding ascii