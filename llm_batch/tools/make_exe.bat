@echo off
rem vupdate.exe 만들기 (인터넷이 되는 Windows PC 에서 한 번만 한다)
rem 준비: Python 3.8 이상,  pip install pyinstaller
cd /d %~dp0\..
py -m PyInstaller --onefile --name vupdate vupdate.py
if errorlevel 1 exit /b 1
echo.
echo dist\vupdate.exe 를 폐쇄망으로 옮긴 뒤  vupdate.exe selftest  로 확인한다
