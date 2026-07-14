@echo off
setlocal
cd /d "%~dp0"

set "PYTHON=venv\Scripts\python.exe"
if not exist "%PYTHON%" (
    echo [错误] 未找到 %PYTHON%，请先创建虚拟环境并安装依赖。
    exit /b 1
)

if not exist ".env" (
    if exist ".env.example" copy ".env.example" ".env" >nul
    echo [错误] 已创建 .env，请填写 OPENAI_API_KEY 后重试。
    exit /b 1
)

for /f "usebackq delims=" %%L in (`"%PYTHON%" setup_test_env.py --emit-cmd`) do %%L
if errorlevel 1 exit /b %errorlevel%

"%PYTHON%" setup_test_env.py --check
if errorlevel 1 exit /b %errorlevel%

if "%~1"=="" (
    echo.
    echo [成功] 环境检查通过。
    exit /b 0
)

set "TEST_SCRIPT=%~1"
shift
"%PYTHON%" -X utf8 "%TEST_SCRIPT%" %*
exit /b %errorlevel%
