@echo off
setlocal EnableExtensions
rem Builds the zero-prerequisite Vigil distribution: no .NET and no Python needed
rem on the target machine.
rem
rem   package.cmd                full run: two publishes + vendored CPython + verify + zip
rem   package.cmd --verify-only  only verify an existing dist\ payload
rem                                (this is what "python smoke_test.py --package" calls)
rem
rem Build machine needs: .NET 10 SDK, Python 3.14, and curl/tar/certutil (built into
rem Win10 1803+). The target machine needs nothing. Do not confuse the two.
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
set "PY_VER=3.14.7"
set "PY_SHA256=d297e5ff019966817ad8502465176139f2d3d840fa4ed84b13bed399a6ab1f15"
set "PY_ZIP=vendor\python-%PY_VER%-embed-amd64.zip"
set "PY_URL=https://www.python.org/ftp/python/%PY_VER%/python-%PY_VER%-embed-amd64.zip"

for /f "delims=" %%v in ('python -X utf8 tools\read_version.py') do set "VER=%%v"
if "%VER%"=="" ( echo [FAIL] cannot read Version from bar\Vigil.csproj & exit /b 1 )
set "NAME=Vigil-%VER%-%RID%"
set "DIST=dist\%NAME%"
echo == %NAME% ==

if /i "%~1"=="--verify-only" goto verify

echo [1/6] publish app (self-contained %RID%)
dotnet publish bar\Vigil.csproj -c Release -r %RID% --self-contained true -p:Version=%VER% -o "%DIST%\app" --nologo > pkg-bar.log 2>&1
if errorlevel 1 ( echo [FAIL] app publish failed, see pkg-bar.log & exit /b 1 )
call :assert_clean pkg-bar.log app || exit /b 1

echo [2/6] vendor CPython %PY_VER%
if not exist vendor mkdir vendor
if not exist "%PY_ZIP%" (
  echo   downloading %PY_URL%
  curl -L --fail --retry 3 -sS -o "%PY_ZIP%" "%PY_URL%"
  if errorlevel 1 ( echo [FAIL] download failed & exit /b 1 )
)
set "GOT="
for /f "skip=1 delims=" %%h in ('certutil -hashfile "%PY_ZIP%" SHA256') do if not defined GOT set "GOT=%%h"
if /i not "%GOT%"=="%PY_SHA256%" ( echo [FAIL] SHA256 mismatch: %GOT% & exit /b 1 )
if exist "%DIST%\app\runtime\python" rmdir /s /q "%DIST%\app\runtime\python"
mkdir "%DIST%\app\runtime\python"
"%TAR%" -xf "%PY_ZIP%" -C "%DIST%\app\runtime\python"
if errorlevel 1 ( echo [FAIL] unzip failed & exit /b 1 )

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
