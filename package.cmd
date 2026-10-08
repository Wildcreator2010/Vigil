@echo off
setlocal EnableExtensions
rem Builds the zero-prerequisite Vigil distribution: no .NET and no Python needed
rem on the target machine. The state engine is vigil-engine.exe (C++, its own
rem zstd decoder), so nothing interpreter-shaped ships any more.
rem
rem   package.cmd                full run: engine + two publishes + verify + zip
rem   package.cmd --verify-only  only verify an existing dist\ payload
rem                                (this is what "python smoke_test.py --package" calls)
rem
rem Build machine needs: .NET 10 SDK, MSVC (for engine\build.cmd), Python 3.14 for
rem the tooling under tools\, and curl/tar/certutil (built into Win10 1803+).
rem The target machine needs nothing. Do not confuse the two.
rem
rem THIS FILE MUST STAY PURE ASCII. cmd.exe reads batch files in the OEM codepage
rem (GBK on zh-CN), and UTF-8 Chinese inside a `rem` line swallows the line break --
rem the tail of the comment then runs as a command and the steps before it get
rem skipped. Measured, not theorised. Chinese text belongs in the Python tools below.
cd /d "%~dp0"

set "RID=win-x64"
rem Always the system bsdtar: a Git Bash on PATH brings its own GNU tar,
rem which cannot read a .zip and dies with "does not look like a tar archive".
set "TAR=%SystemRoot%\System32\tar.exe"

for /f "delims=" %%v in ('python -X utf8 tools\read_version.py') do set "VER=%%v"
if "%VER%"=="" ( echo [FAIL] cannot read Version from bar\Vigil.csproj & exit /b 1 )
set "NAME=Vigil-%VER%-%RID%"
set "DIST=dist\%NAME%"
echo == %NAME% ==

if /i "%~1"=="--verify-only" goto verify

echo [1/6] build native state engine
rem The payload ships vigil-engine.exe instead of a 24MB vendored CPython: the
rem C++ engine carries its own zstd decoder, so the target machine needs no
rem interpreter at all. dsh_state.py stays in the payload as the fallback level
rem (see bar/Engine.cs) -- it is 40KB, the interpreter was not.
rem engine\build.cmd 必须以 call 调：批处理里不加 call 就是把控制权交出去不再回来，
rem 整条流水线在 [1/6] 之后静默结束 —— 实测红过一次（那次连 [2/6] 都没打印）。
call engine\build.cmd > pkg-engine.log 2>&1
if errorlevel 1 ( echo [FAIL] engine build failed, see pkg-engine.log & exit /b 1 )
if not exist "engine\vigil-engine.exe" ( echo [FAIL] engine\build.cmd left no vigil-engine.exe & exit /b 1 )

echo [2/6] publish app (self-contained %RID%)
dotnet publish bar\Vigil.csproj -c Release -r %RID% --self-contained true -p:Version=%VER% -o "%DIST%\app" --nologo > pkg-bar.log 2>&1
if errorlevel 1 ( echo [FAIL] app publish failed, see pkg-bar.log & exit /b 1 )
call :assert_clean pkg-bar.log app || exit /b 1
copy /y "engine\vigil-engine.exe" "%DIST%\app\vigil-engine.exe" >nul
if errorlevel 1 ( echo [FAIL] copy vigil-engine.exe into payload failed & exit /b 1 )

echo [3/6] publish setup wizard (net48)
dotnet publish setup\Vigil.Setup.csproj -c Release -p:Version=%VER% -o "dist\setup-out" --nologo > pkg-setup.log 2>&1
if errorlevel 1 ( echo [FAIL] setup publish failed, see pkg-setup.log & exit /b 1 )
call :assert_clean pkg-setup.log setup || exit /b 1
copy /y "dist\setup-out\Vigil-Setup.exe" "%DIST%\Vigil-Setup.exe" >nul
if errorlevel 1 ( echo [FAIL] copy wizard to dist root failed & exit /b 1 )
copy /y "dist\setup-out\Vigil-Setup.exe" "%DIST%\app\Vigil-Setup.exe" >nul
if errorlevel 1 ( echo [FAIL] copy wizard into payload failed & exit /b 1 )

echo [4/6] license and install note
copy /y LICENSE "%DIST%\LICENSE.txt" >nul
if errorlevel 1 ( echo [FAIL] copy LICENSE failed & exit /b 1 )
copy /y THIRD-PARTY-NOTICES.md "%DIST%\THIRD-PARTY-NOTICES.md" >nul
if errorlevel 1 ( echo [FAIL] copy notices failed & exit /b 1 )
python -X utf8 tools\collect_licenses.py "%DIST%"
if errorlevel 1 ( echo [FAIL] collect third-party license texts failed & exit /b 1 )
python -X utf8 tools\write_readme_install.py "%DIST%"
if errorlevel 1 ( echo [FAIL] write install note failed & exit /b 1 )

:verify
echo [5/6] verify payload
python -X utf8 tools\verify_package.py "%DIST%"
if errorlevel 1 ( echo [FAIL] payload verification failed & exit /b 1 )
if /i "%~1"=="--verify-only" exit /b 0

echo [6/6] zip
if exist "dist\%NAME%.zip" del /q "dist\%NAME%.zip"
"%TAR%" -a -cf "dist\%NAME%.zip" -C dist "%NAME%"
if errorlevel 1 ( echo [FAIL] zip failed & exit /b 1 )
for %%F in ("dist\%NAME%.zip") do echo [OK] dist\%NAME%.zip %%~zF bytes
exit /b 0


rem findstr's exit code is inverted here: 0 means "found", i.e. the build had
rem errors or warnings, and a warning is a failure by this repo's gate.
:assert_clean
findstr /c:": error " "%~1" >nul
if not errorlevel 1 ( echo [FAIL] %~2 build has errors & exit /b 1 )
findstr /c:": warning " "%~1" >nul
if not errorlevel 1 ( echo [FAIL] %~2 build has warnings & exit /b 1 )
exit /b 0
