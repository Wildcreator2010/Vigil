@echo off
rem 编译任务栏状态栏（.NET 10 桌面运行时已装，无需联网）
cd /d "%~dp0bar"
dotnet build -c Release --nologo
if errorlevel 1 goto fail
echo.
echo 已生成：bin\Release\net10.0-windows\Vigil.exe
echo 双击 start-bar.bat 启动。
exit /b 0
:fail
echo 编译失败，请确认已安装 .NET 10 SDK 与 Windows 桌面运行时。
exit /b 1
