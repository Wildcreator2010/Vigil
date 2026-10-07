// 探针：不引第三方库，Windows 自带的 ntdll 压缩 API 能不能解标准 zstd 帧。
//
// 这是整个 C++ 引擎的成败点：dsh 的会话文件是 session.v4.jsonl.zstd，每条 JSONL
// 压成一个独立 zstd 帧。Python 侧靠 3.14 标准库的 compression.zstd，C++ 侧没有等价物
// —— 除非 Windows 自己有。
//
// 本机实测（Win11 10.0.26300）：RtlDecompressBufferEx2 **没导出**，
// RtlDecompressBufferEx / RtlDecompressBuffer / RtlGetCompressionWorkSpaceSize 在。
// 所以格式常量填什么、走哪个入口，全部由这个探针试出来，不靠文档猜。
//
// 编：build-one.bat probe_zstd.cpp probe_zstd.exe
#include <windows.h>
#include <cstdio>
#include <vector>
#include <string>
#include <algorithm>

#ifndef NT_SUCCESS
#define NT_SUCCESS(s) (((NTSTATUS)(s)) >= 0)
#endif

typedef NTSTATUS(WINAPI *GetWsFn)(ULONG format, PULONG ws);
typedef NTSTATUS(WINAPI *Dec2Fn)(ULONG format, USHORT minWinVer, PUCHAR src, ULONG srcLen,
                                 PUCHAR dst, ULONG dstLen, PULONG written,
                                 PVOID ws, ULONG wsLen);
typedef NTSTATUS(WINAPI *DecExFn)(ULONG format, PUCHAR src, ULONG srcLen,
                                  PUCHAR dst, ULONG dstLen, PULONG written);

int main(int argc, char **argv) {
    if (argc < 2) { std::printf("用法: %s <file.zstd>\n", argv[0]); return 2; }

    HANDLE f = CreateFileA(argv[1], GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE,
                           nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (f == INVALID_HANDLE_VALUE) { std::printf("打不开文件 %lu\n", GetLastError()); return 2; }
    LARGE_INTEGER sz{};
    GetFileSizeEx(f, &sz);
    std::vector<unsigned char> src(static_cast<size_t>(sz.QuadPart));
    DWORD got = 0;
    ReadFile(f, src.data(), static_cast<DWORD>(src.size()), &got, nullptr);
    CloseHandle(f);
    src.resize(got);
    if (src.size() < 8) { std::printf("文件太小\n"); return 2; }
    std::printf("输入 %zu 字节，前 4 字节 %02X %02X %02X %02X\n",
                src.size(), src[0], src[1], src[2], src[3]);

    HMODULE nt = GetModuleHandleA("ntdll.dll");
    if (!nt) nt = LoadLibraryA("ntdll.dll");
    auto getWs = reinterpret_cast<GetWsFn>(GetProcAddress(nt, "RtlGetCompressionWorkSpaceSize"));
    auto dec2 = reinterpret_cast<Dec2Fn>(GetProcAddress(nt, "RtlDecompressBufferEx2"));
    auto decEx = reinterpret_cast<DecExFn>(GetProcAddress(nt, "RtlDecompressBufferEx"));
    std::printf("入口：WorkSpaceSize=%p BufferEx2=%p BufferEx=%p\n",
                (void *)getWs, (void *)dec2, (void *)decEx);
    if (!getWs || (!dec2 && !decEx)) { std::printf("入口不全\n"); return 1; }

    // dsh 每条记录一个帧：从第二个魔数切开就是"第一帧"
    size_t next = 0;
    for (size_t i = 4; i + 3 < src.size(); ++i) {
        if (src[i] == 0x28 && src[i + 1] == 0xB5 && src[i + 2] == 0x2F && src[i + 3] == 0xFD) {
            next = i; break;
        }
    }
    std::vector<unsigned char> frame(src.begin(), src.begin() + (next ? next : src.size()));
    std::printf("第一帧 %zu 字节\n", frame.size());

    const ULONG fmts[] = { 0x0900u, 0x0909u, 0x0009u, 0x000Cu };
    for (ULONG fmt : fmts) {
        ULONG wsSize = 0;
        NTSTATUS q = getWs(fmt, &wsSize);
        std::printf("\n格式 %04X -> WorkSpaceSize %08X / %lu 字节\n", fmt, (unsigned)q, wsSize);
        if (!NT_SUCCESS(q) || !wsSize) continue;
        std::vector<unsigned char> ws(wsSize);
        for (size_t cap = 1u << 14; cap <= (1u << 26); cap <<= 1) {
            std::vector<unsigned char> dst(cap);
            ULONG written = 0;
            NTSTATUS st = dec2
                ? dec2(fmt, 0, frame.data(), (ULONG)frame.size(),
                       dst.data(), (ULONG)dst.size(), &written, ws.data(), wsSize)
                : decEx(fmt, frame.data(), (ULONG)frame.size(),
                        dst.data(), (ULONG)dst.size(), &written);
            if (NT_SUCCESS(st)) {
                std::string s(reinterpret_cast<char *>(dst.data()), written);
                std::printf("[OK] 格式 %04X 经 %s 解出 %lu 字节：\n%s\n", fmt,
                            dec2 ? "BufferEx2" : "BufferEx", written,
                            s.substr(0, std::min<size_t>(s.size(), 300)).c_str());
                return 0;
            }
            if (st != (NTSTATUS)0xC0000023L && st != (NTSTATUS)0xC0000017L) {
                std::printf("  %zu 字节缓冲 -> %08X，换下一个格式\n", cap, (unsigned)st);
                break;
            }
        }
    }
    std::printf("\n[FAIL] Windows 自带压缩 API 解不了这个 zstd 帧\n");
    return 1;
}
