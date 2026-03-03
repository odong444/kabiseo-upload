@echo off
chcp 65001 >nul
echo === PC2 서버 자동 세팅 ===

echo [1/4] pip 패키지 설치...
pip install flask requests

echo [2/4] 폴더 생성...
if not exist C:\kabiseo mkdir C:\kabiseo

echo [3/4] 서버 파일 다운로드...
curl -o C:\kabiseo\task_queue_server.py https://raw.githubusercontent.com/odong444/kabiseo-upload/master/task_queue_server.py

echo [4/4] 방화벽 포트 5050 개방...
netsh advfirewall firewall add rule name="TaskQueue5050" dir=in action=allow protocol=TCP localport=5050

echo.
echo === 세팅 완료! ===
echo 서버 시작: python C:\kabiseo\task_queue_server.py
echo.
pause
