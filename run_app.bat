@echo off
TITLE MindFlow AI - Starter
echo ══════════════════════════════════════════════════
echo   MindFlow: AI Stress-Aware Task Manager
echo ══════════════════════════════════════════════════
echo.

:: 1. Start the AI Bridge in a new window
echo [1/2] Starting AI Bridge (Python in mindflow_env)...
start "MindFlow AI Bridge" cmd /k "mindflow_env\Scripts\activate && cd realtime && python bridge.py"

:: 2. Wait a few seconds for the bridge to initialize
timeout /t 5 /nobreak > nul

:: 3. Start the Node.js Server in a new window
echo [2/2] Starting Backend Server (Node.js)...
start "MindFlow Backend" cmd /k "node server.js"

echo.
echo 🚀 Both services are starting! 
echo 🌐 Opening dashboard in your browser...
echo.

:: 4. Automatically open the browser
timeout /t 3 /nobreak > nul
start "" "http://127.0.0.1:3000/dashboard.html"

exit
