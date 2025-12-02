@echo off
echo ===============================
echo Building video_cropper EXE...
echo ===============================

REM ---- Activate virtual environment (optional, remove if not needed) ----
CALL .venv\Scripts\activate

REM ---- Path to VLC installation ----
SET "VLC_PATH=C:\Program Files\VideoLAN\VLC"

REM ---- Run PyInstaller ----
pyinstaller ^
 --noconsole ^
 --onefile ^
 --name video_cropper ^
 --hidden-import=vlc ^
 --add-data "%VLC_PATH%\libvlc.dll;." ^
 --add-data "%VLC_PATH%\libvlccore.dll;." ^
 --add-data "%VLC_PATH%\plugins;plugins" ^
 video_cropper\app.py

echo.
echo Build completed! EXE is located in: dist\video_cropper.exe
pause
