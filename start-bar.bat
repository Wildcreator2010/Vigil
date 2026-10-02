@echo off
rem 启动 dsh 任务栏状态栏；没有编译过就先编译
setlocal
set "EXE=%~dp0bar\bin\Release\net10.0-windows\DshBar.exe"
if not exist "%EXE%" (
  pushd "%~dp0bar"
  call dotnet build -c Release --nologo
  popd
)
start "" "%EXE%"
endlocal
