@echo off
REM ============================================================
REM 命令行构建 Chapter1.ConnectToSw（无需打开 Visual Studio）
REM ============================================================
setlocal

set "MSBUILD=C:\Program Files\Microsoft Visual Studio\18\Community\MSBuild\Current\Bin\MSBuild.exe"
set "PROJ=%~dp0Chapter1.ConnectToSw\Chapter1.ConnectToSw.csproj"

if not exist "%MSBUILD%" (
    echo [错误] 未找到 MSBuild: %MSBUILD%
    echo 请用 where /r "C:\Program Files\Microsoft Visual Studio" MSBuild.exe 查找实际路径后修改本脚本。
    exit /b 1
)

REM 若服务正在运行，exe 会被锁定导致构建失败，先提示
tasklist /fi "imagename eq Chapter1.ConnectToSw.exe" 2>nul | find /i "Chapter1.ConnectToSw.exe" >nul
if %errorlevel%==0 (
    echo [警告] Chapter1.ConnectToSw.exe 正在运行，构建可能因 exe 被锁定而失败。
    echo        请先关闭正在运行的服务窗口后重试。
)

echo [构建] 正在编译 Chapter1.ConnectToSw ...
"%MSBUILD%" "%PROJ%" /p:Configuration=Debug /v:minimal /nologo /t:Rebuild
if %errorlevel% neq 0 (
    echo [失败] 构建失败，退出码 %errorlevel%
    exit /b %errorlevel%
)

echo.
echo [成功] 输出: %~dp0Chapter1.ConnectToSw\bin\Debug\Chapter1.ConnectToSw.exe
endlocal
