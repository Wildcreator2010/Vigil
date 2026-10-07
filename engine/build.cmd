@echo off
rem Build the Vigil state engine. PURE ASCII on purpose: cmd.exe reads batch files
rem in the OEM codepage (GBK on zh-CN), so UTF-8 Chinese inside a rem line swallows
rem the newline and the tail runs as a command. Same rule as package.cmd.
rem
rem   build.cmd            -> vigil-engine.exe
rem   build.cmd clean      -> delete artifacts first
rem
rem Vendored zstd decompress-only subset lives in third_party\zstd (see its LICENSE).
rem Windows' own compression API cannot decode zstd on this machine (probe_zstd.cpp
rem proved it), so the decoder is compiled in: ~1MB of source, ~350KB of exe.
setlocal EnableExtensions
cd /d "%~dp0"

if "%~1"=="clean" del /q vigil-engine.exe *.obj 2>nul

call "C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\VC\Auxiliary\Build\vcvars64.bat" > vcvars.log 2>&1
if errorlevel 1 ( echo [FAIL] vcvars64 did not load, see vcvars.log & exit /b 1 )

cl /nologo /EHsc /W3 /O2 /std:c++20 /utf-8 ^
   /D "ZSTD_DISABLE_ASM=1" /D "_CRT_SECURE_NO_WARNINGS" ^
   /I third_party\zstd ^
   vigil_engine.cpp ^
   third_party\zstd\common\debug.c ^
   third_party\zstd\common\entropy_common.c ^
   third_party\zstd\common\error_private.c ^
   third_party\zstd\common\fse_decompress.c ^
   third_party\zstd\common\pool.c ^
   third_party\zstd\common\threading.c ^
   third_party\zstd\common\xxhash.c ^
   third_party\zstd\common\zstd_common.c ^
   third_party\zstd\decompress\huf_decompress.c ^
   third_party\zstd\decompress\zstd_ddict.c ^
   third_party\zstd\decompress\zstd_decompress.c ^
   third_party\zstd\decompress\zstd_decompress_block.c ^
   /Fe:vigil-engine.exe
if errorlevel 1 ( echo [FAIL] compile & exit /b 1 )

del /q *.obj 2>nul
echo [OK] vigil-engine.exe
exit /b 0
