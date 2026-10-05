@echo off
REM ============================================================
REM 命令行启动 SolidWorks HTTP API 服务（无需打开 Visual Studio）
REM 注意：运行前请先启动 SolidWorks，否则程序会提示退出。
REM 服务会常驻监听 http://localhost:5100/ ，按 Ctrl+C 停止。
REM ============================================================
setlocal

set "EXE=%~dp0Chapter1.ConnectToSw\bin\Debug\Chapter1.ConnectToSw.exe"

if not exist "%EXE%" (
    echo [错误] 未找到可执行文件: %EXE%
    echo        请先运行 build_service.bat 进行构建。
    exit /b 1
)

REM 检查 SolidWorks 是否已启动
tasklist /fi "imagename eq SLDWORKS.exe" 2>nul | find /i "SLDWORKS.exe" >nul
if %errorlevel% neq 0 (
    echo [警告] 未检测到 SolidWorks 进程，请先启动 SolidWorks。
)

echo [启动] %EXE%
echo [提示] 按 Ctrl+C 停止服务。
"%EXE%"
endlocal