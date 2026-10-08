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
rem
rem Two compile passes, deliberately different strictness:
rem   vigil_engine.cpp   /W4 /WX   first-party code, warnings are build failures
rem   third_party\zstd   /w        upstream C decoder, not ours to keep quiet
rem _CRT_SECURE_NO_WARNINGS is only handed to the vendor pass now. Passing it to
rem vigil_engine.cpp too would mute C4996 (and everything else) across our own
rem source, which is how a getenv() returning a dangling pointer would get in.
setlocal EnableExtensions
cd /d "%~dp0"

if "%~1"=="clean" del /q vigil-engine.exe *.obj 2>nul

call "C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\VC\Auxiliary\Build\vcvars64.bat" > vcvars.log 2>&1
if errorlevel 1 ( echo [FAIL] vcvars64 did not load, see vcvars.log & exit /b 1 )

cl /nologo /EHsc /W4 /WX /O2 /std:c++20 /utf-8 ^
   /D "ZSTD_DISABLE_ASM=1" ^
   /I third_party\zstd ^
   /c vigil_engine.cpp
if errorlevel 1 ( echo [FAIL] vigil_engine.cpp did not compile clean & exit /b 1 )

cl /nologo /O2 /utf-8 /w ^
   /D "ZSTD_DISABLE_ASM=1" /D "_CRT_SECURE_NO_WARNINGS" /D "_CRT_NONSTDC_NO_DEPRECATE" ^
   /I third_party\zstd ^
   /c ^
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
   third_party\zstd\decompress\zstd_decompress_block.c
if errorlevel 1 ( echo [FAIL] vendored zstd did not compile & exit /b 1 )

link /nologo /OUT:vigil-engine.exe *.obj crypt32.lib winhttp.lib
if errorlevel 1 ( echo [FAIL] link & exit /b 1 )

del /q *.obj 2>nul
echo [OK] vigil-engine.exe
exit /b 0
