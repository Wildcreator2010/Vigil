@echo off
setlocal EnableExtensions
rem Builds the zero-prerequisite Vigil distribution (no .NET / no Python on the
rem target machine). Two flavours ship from one source tree; what differs between
rem them is only the release parameters, so any measured difference belongs to the
rem payload and not to the product code.
rem
rem   cpp     (default)  1.1.0 "Lilium"                vigil-engine.exe, C++ engine
rem   python             1.0.0 "Vachellia farnesiana"  vendored CPython 3.14.7
rem   both               python first, then cpp
rem
rem The identity (version / codename / slug) is read from tools\flavors.py, and
rem verify_package.py runs the payload's own `Vigil.exe --engine-probe` against
rem that same table. A payload that ships the wrong engine, or claims to be the
rem other release, fails there instead of on somebody's machine.
rem
rem   package.cmd [cpp|python|both]            full run
rem   package.cmd --verify-only                verify every dist\ payload that exists
rem                                              (this is what smoke_test.py --package calls)
rem
rem Build machine needs: .NET 10 SDK, MSVC (engine\build.cmd, cpp flavour only),
rem Python 3.14 for the tooling under tools\, and curl/tar/certutil (Win10 1803+).
rem The python flavour also needs network on the first run, to fetch the
rem embeddable interpreter into vendor\ (cached there afterwards).
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
set "PY_VER=3.14.7"
set "PY_SHA256=d297e5ff019966817ad8502465176139f2d3d840fa4ed84b13bed399a6ab1f15"
set "PY_ZIP=vendor\python-%PY_VER%-embed-amd64.zip"
set "PY_URL=https://www.python.org/ftp/python/%PY_VER%/python-%PY_VER%-embed-amd64.zip"

set "FLAVOR=cpp"
set "DO_VERIFY="
:argloop
if "%~1"=="" goto argdone
if /i "%~1"=="--verify-only" ( set "DO_VERIFY=1" & shift & goto argloop )
if /i "%~1"=="cpp"            ( set "FLAVOR=cpp" & shift & goto argloop )
if /i "%~1"=="python"         ( set "FLAVOR=python" & shift & goto argloop )
if /i "%~1"=="both"           ( set "FLAVOR=both" & shift & goto argloop )
echo [FAIL] unknown argument: %~1
echo        usage: package.cmd [cpp^|python^|both] [--verify-only]
exit /b 2
:argdone

rem No parenthesised blocks here on purpose: a whole block is expanded in one go,
rem so `%errorlevel%` on the last line of a block is the value from *before* it ran.
rem That would turn a failed verification into "exit /b 0" -- the gates would then
rem grade a payload that never passed. Labels re-read the line when it executes.
if defined DO_VERIFY goto verify_all
if "%FLAVOR%"=="both" goto build_both
call :one %FLAVOR%
exit /b %errorlevel%

:build_both
call :one python
if errorlevel 1 exit /b 1
call :one cpp
exit /b %errorlevel%

:verify_all
call :verify_one python
if errorlevel 1 exit /b 1
call :verify_one cpp
exit /b %errorlevel%


rem ---------------------------------------------------------------- one flavour
rem Lines inside a parenthesised block are expanded when the block is parsed, so
rem anything that reads a variable set by a command in the same block (GOT= below)
rem lives in its own subroutine. Two subroutines for exactly that reason.
:one
set "F=%~1"
set "VER=" & set "CODE=" & set "SLUG=" & set "NAME="
for /f "delims=" %%v in ('python -X utf8 tools\flavors.py %F% version')  do set "VER=%%v"
for /f "delims=" %%v in ('python -X utf8 tools\flavors.py %F% codename') do set "CODE=%%v"
for /f "delims=" %%v in ('python -X utf8 tools\flavors.py %F% slug')      do set "SLUG=%%v"
for /f "delims=" %%v in ('python -X utf8 tools\flavors.py %F% dir')       do set "NAME=%%v"
if "%VER%"==""  ( echo [FAIL] tools\flavors.py gave no version for %F% & exit /b 1 )
if "%CODE%"=="" ( echo [FAIL] tools\flavors.py gave no codename for %F% & exit /b 1 )
if "%SLUG%"=="" ( echo [FAIL] tools\flavors.py gave no slug for %F% & exit /b 1 )
if "%NAME%"=="" ( echo [FAIL] tools\flavors.py gave no dir name for %F% & exit /b 1 )
set "DIST=dist\%NAME%"
echo == %NAME% ==  [flavour=%F%]

rem A payload is only evidence about the run that produced it if nothing of the
rem previous run survives inside it. Measured the hard way once: an app\ tree that
rem still held the previous flavour's engine made "which engine ships" unanswerable.
if exist "%DIST%\app" rmdir /s /q "%DIST%\app"
if exist "%DIST%\Vigil-Setup.exe" del /q "%DIST%\Vigil-Setup.exe"

echo [1/6] state engine
if /i "%F%"=="cpp" (
  rem engine\build.cmd MUST be called with `call`: without it the batch hands over
  rem control and never comes back -- the whole pipeline then ends silently after
  rem step 1 without even printing step 2. Observed.
  call engine\build.cmd > pkg-engine.log 2>&1
  if errorlevel 1 ( echo [FAIL] engine build failed, see pkg-engine.log & exit /b 1 )
) else (
  echo   native engine not built: this flavour ships the interpreter instead
)

echo [2/6] publish app (self-contained %RID%)
dotnet publish bar\Vigil.csproj -c Release -r %RID% --self-contained true -p:Version=%VER% -p:ProductCodename="%CODE%" -p:ProductSlug=%SLUG% -o "%DIST%\app" --nologo > pkg-bar.log 2>&1
if errorlevel 1 ( echo [FAIL] app publish failed, see pkg-bar.log & exit /b 1 )
call :assert_clean pkg-bar.log app || exit /b 1

if /i "%F%"=="cpp" (
  copy /y "engine\vigil-engine.exe" "%DIST%\app\vigil-engine.exe" >nul
  if errorlevel 1 ( echo [FAIL] copy vigil-engine.exe into payload failed & exit /b 1 )
) else (
  call :vendor_python || exit /b 1
)

echo [3/6] publish setup wizard (net48)
dotnet publish setup\Vigil.Setup.csproj -c Release -p:Version=%VER% -p:ProductCodename="%CODE%" -p:ProductSlug=%SLUG% -o "dist\setup-out" --nologo > pkg-setup.log 2>&1
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

echo [5/6] verify payload
python -X utf8 tools\verify_package.py "%DIST%"
if errorlevel 1 ( echo [FAIL] payload verification failed & exit /b 1 )

echo [6/6] zip
if exist "dist\%NAME%.zip" del /q "dist\%NAME%.zip"
"%TAR%" -a -cf "dist\%NAME%.zip" -C dist "%NAME%"
if errorlevel 1 ( echo [FAIL] zip failed & exit /b 1 )
for %%F in ("dist\%NAME%.zip") do echo [OK] dist\%NAME%.zip %%~zF bytes
exit /b 0


rem ------------------------------------------------------------ vendored CPython
rem The 1.0.0 line's engine is dsh_state.py on an interpreter the payload carries:
rem 24MB that buys "no Python required on the target machine". The checksum is not
rem ceremony -- an unverified download is how a tampered interpreter ships to users.
:vendor_python
if not exist vendor mkdir vendor
if not exist "%PY_ZIP%" (
  echo   downloading %PY_URL%
  curl -L --fail --retry 3 -sS -o "%PY_ZIP%" "%PY_URL%"
  if errorlevel 1 ( echo [FAIL] download failed & exit /b 1 )
)
set "GOT="
for /f "skip=1 delims=" %%h in ('certutil -hashfile "%PY_ZIP%" SHA256') do if not defined GOT set "GOT=%%h"
if /i not "%GOT%"=="%PY_SHA256%" ( echo [FAIL] SHA256 mismatch: %GOT% & exit /b 1 )
mkdir "%DIST%\app\runtime\python"
"%TAR%" -xf "%PY_ZIP%" -C "%DIST%\app\runtime\python"
if errorlevel 1 ( echo [FAIL] unzip failed & exit /b 1 )
exit /b 0


rem ------------------------------------------------------------------- verify only
rem Every known flavour that exists on disk, each against its own expected shape.
rem Not the loose `Vigil-*-win-x64` glob: pre-codename leftovers from earlier runs
rem would then be graded against a flavour table they were never built from.
:verify_one
set "F=%~1"
set "NAME="
for /f "delims=" %%v in ('python -X utf8 tools\flavors.py %F% dir') do set "NAME=%%v"
if "%NAME%"=="" ( echo [FAIL] tools\flavors.py returned no dir name for %F% & exit /b 1 )
if not exist "dist\%NAME%\app" (
  echo [SKIP] dist\%NAME% not built ^(run: package.cmd %F%^)
  exit /b 0
)
python -X utf8 tools\verify_package.py "dist\%NAME%"
exit /b %errorlevel%


rem findstr's exit code is inverted here: 0 means "found", i.e. the build had
rem errors or warnings, and a warning is a failure by this repo's gate.
:assert_clean
findstr /c:": error " "%~1" >nul
if not errorlevel 1 ( echo [FAIL] %~2 build has errors & exit /b 1 )
findstr /c:": warning " "%~1" >nul
if not errorlevel 1 ( echo [FAIL] %~2 build has warnings & exit /b 1 )
exit /b 0
