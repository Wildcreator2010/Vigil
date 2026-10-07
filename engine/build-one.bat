@echo off
rem Pure ASCII on purpose: cmd.exe reads batch files in the OEM codepage (GBK on
rem zh-CN), so UTF-8 Chinese in a rem line swallows the newline and the tail runs
rem as a command. Same rule as package.cmd.
call "C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\VC\Auxiliary\Build\vcvars64.bat" > vcenv.log 2>&1
cd /d "%~dp0"
cl /nologo /EHsc /W4 /std:c++20 /utf-8 %1 /Fe:%2
