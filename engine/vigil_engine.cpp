// Vigil 状态引擎（C++）：读 dsh 会话 → 解压 zstd → 判定状态 → 出一帧快照 JSON。
//
// 目标是替掉随产物分发的 24MB CPython。产物 vigil-engine.exe 约 400KB。
//
// 三条入口：
//   vigil-engine --session <会话文件> <mtime> <now>     单会话判定（自己解压）
//   vigil-engine --classify <mtime> <now> < records     单会话判定（吃已解压明文）
//   vigil-engine --snapshot <now> [--no-balance]        整帧快照，形状等于 python --json
//
// 与 dsh_state.py 的对应关系是逐函数的：_read_bytes / _iter_records / read_session /
// classify / scan / pick_primary / _session_rows / compose_text / snapshot。
// 手搬必错，所以判据不是"看着一样"：engine/compare_classify.py 拿真实会话逐字段比，
// 下一层再加快照级对照（--snapshot vs python --json）。
//
// 两处刻意的实现约束：
// · 截断一律按 **UTF-8 字符**而不是字节 —— Python 的 title[:80] / text[:120] /
//   clamp_tip(63) 都是字符切片，按字节切会把中文劈成半个字，界面直接出乱码。
// · last_todo / last_turn_end 深拷一份，不存进尾部窗口的指针（元素被挤出就悬垂）。
#include <algorithm>
#include <cctype>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <deque>
#include <fstream>
#include <iostream>
#include <map>
#include <memory>
#include <string>
#include <type_traits>
#include <vector>

#if defined(_WIN32)
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <tlhelp32.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <io.h>
#endif

#include "third_party/zstd/zstd.h"
#include "third_party/zstd/zstd_errors.h"

// ---------------------------------------------------------------- 常量表
// 与 dsh_state.py 的 STATES、bar/Panel/Ui.cs 的 StateMap 三方逐项一致；
// 冒烟有门禁钉着"前端映射逐项等于引擎 STATES"，改一边必红。
struct StateInfo { const char *code; const char *label; const char *glyph; const char *color; int priority; };
static const StateInfo kStates[] = {
    {"needs_action", "需要操作", "!", "#D87D44", 90},
    {"error",        "出错了",   "X", "#C65B51", 80},
    {"stalled",      "疑似卡住", "S", "#C3A559", 70},
    {"thinking",     "正在思考", "T", "#92A5A0", 60},
    {"tool_running", "正在执行工具", "W", "#54644A", 58},
    {"answering",    "正在回答", "A", "#7F8E6B", 56},
    {"done",         "回答完成", "D", "#BFD268", 40},
    {"aborted",      "已中断",   "K", "#5A6363", 30},
    {"idle",         "待命",     "I", "#D2D2C8", 20},
    {"offline",      "未运行",   "O", "#2E3844", 10},
    {"unknown",      "未知",     "?", "#8E918A", 0},
};
static const StateInfo *StateOf(const std::string &code) {
    for (const auto &s : kStates) if (code == s.code) return &s;
    return &kStates[sizeof kStates / sizeof kStates - 1];   // unknown
}

static const double kActiveWindow = 300.0;
static const double kDoneWindow = 25.0;
static const double kStallWindow = 120.0;
static const double kWaitPromoteWindow = 2 * 3600.0;
static const double kStaleAlertWindow = 24 * 3600.0;
static const size_t kTailEvents = 500;
static const size_t kSessionsInSnapshot = 60;
static const char *kSessionFile = "session.v4.jsonl.zstd";
static const char *kAppExes[] = {"deepseek harness.exe", "dsh.exe"};
static const std::map<std::string, std::string> kBlockingTools = {
    {"ask_user_question", "问题"},
    {"exit_plan_mode", "计划确认"},
};

// ---------------------------------------------------------------- UTF-8 长度
// Python 的一切截断都是按字符；这里必须同口径，否则中文会被劈成半个字。
static size_t Utf8Len(const std::string &s) {
    size_t n = 0;
    for (size_t i = 0; i < s.size(); ++i) if ((s[i] & 0xC0) != 0x80) ++n;
    return n;
}

static std::string Utf8Trunc(const std::string &s, size_t maxChars) {
    if (Utf8Len(s) <= maxChars) return s;
    size_t n = 0, i = 0;
    while (i < s.size()) {
        size_t step = 1;
        unsigned char c = (unsigned char)s[i];
        if ((c & 0xE0) == 0xC0) step = 2;
        else if ((c & 0xF0) == 0xE0) step = 3;
        else if ((c & 0xF8) == 0xF0) step = 4;
        if (n + 1 > maxChars) break;
        i += step; ++n;
    }
    return s.substr(0, i);
}

// clamp_tip 的第一步是 " ".join(text.split())：把任意空白压成单个空格
static std::string CollapseSpaces(const std::string &s) {
    std::string r;
    bool pending = false;
    for (char c : s) {
        if (c == ' ' || c == '\t' || c == '\r' || c == '\n' || c == '\f' || c == '\v') {
            pending = !r.empty();
            continue;
        }
        if (pending) { r += ' '; pending = false; }
        r += c;
    }
    return r;
}

static std::string ClampTip(const std::string &text, size_t limit = 63) {
    std::string s = CollapseSpaces(text);
    if (Utf8Len(s) <= limit) return s;
    return Utf8Trunc(s, limit - 1) + "…";
}

// ---------------------------------------------------------------- 极简 JSON 读
struct JValue {
    enum Kind { Null, Bool, Num, Str, Arr, Obj } kind = Null;
    bool b = false;
    double num = 0;
    std::string str;
    std::vector<JValue> arr;
    std::vector<std::pair<std::string, JValue>> obj;      // 保序；会话记录字段少，线性找够用

    const JValue *at(const std::string &key) const {
        if (kind != Obj) return nullptr;
        for (const auto &kv : obj)
            if (kv.first == key && kv.second.kind != Null) return &kv.second;
        return nullptr;
    }
    std::string asStr() const { return kind == Str ? str : std::string(); }
};

namespace json {
struct Parser {
    const std::string &s;
    size_t i = 0;
    bool Fail = false;
    explicit Parser(const std::string &src) : s(src) {}

    void Skip() { while (i < s.size() && (s[i] == ' ' || s[i] == '\t' || s[i] == '\r' || s[i] == '\n')) ++i; }

    void Value(JValue &v) {
        Skip();
        if (i >= s.size()) { Fail = true; return; }
        char c = s[i];
        if (c == '{') return Obj(v);
        if (c == '[') return Arr(v);
        if (c == '"') { v.kind = JValue::Str; Str(v.str); return; }
        if (c == 't') { v.kind = JValue::Bool; v.b = true; i += 4; return; }
        if (c == 'f') { v.kind = JValue::Bool; v.b = false; i += 5; return; }
        if (c == 'n') { v.kind = JValue::Null; i += 4; return; }
        v.kind = JValue::Num;
        size_t start = i;
        while (i < s.size() && (isdigit((unsigned char)s[i]) || s[i] == '-' || s[i] == '+' ||
                                s[i] == '.' || s[i] == 'e' || s[i] == 'E')) ++i;
        if (i == start) { Fail = true; return; }
        v.num = strtod(s.substr(start, i - start).c_str(), nullptr);
    }
    void Str(std::string &out) {
        if (i >= s.size() || s[i] != '"') { Fail = true; return; }
        ++i;
        std::string r;
        while (i < s.size()) {
            char c = s[i++];
            if (c == '"') { out = r; return; }
            if (c != '\\') { r += c; continue; }
            if (i >= s.size()) break;
            char e = s[i++];
            switch (e) {
                case 'n': r += '\n'; break;
                case 't': r += '\t'; break;
                case 'r': r += '\r'; break;
                case 'b': r += '\b'; break;
                case 'f': r += '\f'; break;
                case '"': r += '"'; break;
                case '\\': r += '\\'; break;
                case '/': r += '/'; break;
                case 'u': {
                    if (i + 4 > s.size()) { Fail = true; return; }
                    unsigned cp = (unsigned)strtoul(s.substr(i, 4).c_str(), nullptr, 16);
                    i += 4;
                    if (cp >= 0xD800 && cp < 0xDC00 && i + 6 <= s.size() && s[i] == '\\' && s[i + 1] == 'u') {
                        unsigned lo = (unsigned)strtoul(s.substr(i + 2, 4).c_str(), nullptr, 16);
                        if (lo >= 0xDC00 && lo < 0xE000) {           // 代理对合成一个码点
                            cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00);
                            i += 6;
                        }
                    }
                    if (cp < 0x80) r += (char)cp;
                    else if (cp < 0x800) { r += (char)(0xC0 | (cp >> 6)); r += (char)(0x80 | (cp & 0x3F)); }
                    else if (cp < 0x10000) {
                        r += (char)(0xE0 | (cp >> 12)); r += (char)(0x80 | ((cp >> 6) & 0x3F));
                        r += (char)(0x80 | (cp & 0x3F));
                    } else {
                        r += (char)(0xF0 | (cp >> 18)); r += (char)(0x80 | ((cp >> 12) & 0x3F));
                        r += (char)(0x80 | ((cp >> 6) & 0x3F)); r += (char)(0x80 | (cp & 0x3F));
                    }
                    break;
                }
                default: r += e;
            }
        }
        Fail = true;
    }
    void Arr(JValue &v) {
        v.kind = JValue::Arr; ++i;
        Skip();
        if (i < s.size() && s[i] == ']') { ++i; return; }
        while (i < s.size()) {
            JValue e; Value(e);
            if (Fail) return;
            v.arr.push_back(std::move(e));
            Skip();
            if (i < s.size() && s[i] == ',') { ++i; continue; }
            if (i < s.size() && s[i] == ']') { ++i; return; }
            Fail = true; return;
        }
        Fail = true;
    }
    void Obj(JValue &v) {
        v.kind = JValue::Obj; ++i;
        Skip();
        if (i < s.size() && s[i] == '}') { ++i; return; }
        while (i < s.size()) {
            Skip();
            std::string k; Str(k);
            if (Fail) return;
            Skip();
            if (i >= s.size() || s[i] != ':') { Fail = true; return; }
            ++i;
            JValue val; Value(val);
            if (Fail) return;
            v.obj.emplace_back(std::move(k), std::move(val));
            Skip();
            if (i < s.size() && s[i] == ',') { ++i; continue; }
            if (i < s.size() && s[i] == '}') { ++i; return; }
            Fail = true; return;
        }
        Fail = true;
    }
};
static bool Parse(const std::string &src, JValue &out) {
    Parser p(src); p.Value(out); return !p.Fail;
}
static void EmitStr(std::string &o, const std::string &s) {
    o += '"';
    for (unsigned char c : s) {
        if (c == '"') o += "\\\"";
        else if (c == '\\') o += "\\\\";
        else if (c == '\n') o += "\\n";
        else if (c == '\r') o += "\\r";
        else if (c == '\t') o += "\\t";
        else if (c < 0x20) { char buf[8]; snprintf(buf, sizeof buf, "\\u%04X", c); o += buf; }
        else o += (char)c;                      // 非 ASCII 原样出：与 ensure_ascii=False 同口径
    }
    o += '"';
}
static void EmitNum(std::string &o, double v) {
    char buf[64];
    if (v == (double)(long long)v) snprintf(buf, sizeof buf, "%lld", (long long)v);
    else snprintf(buf, sizeof buf, "%.10g", v);
    o += buf;
}
}  // namespace json

// ---------------------------------------------------------------- zstd 解压
// 照 _read_bytes 一比一搬：dsh 把**每条 JSONL 记录**压成独立帧，所以按魔数切帧逐帧解；
// 正在写入的最后一帧可能不完整，跳帧保住前面所有记录。首帧就失败时退回整条流的
// 解法（_whole_stream：分块读到哪算哪，末尾半帧丢弃）。
static bool IsMagic(const std::vector<unsigned char> &b, size_t i) {
    return i + 3 < b.size() && b[i] == 0x28 && b[i + 1] == 0xB5 && b[i + 2] == 0x2F && b[i + 3] == 0xFD;
}

static bool DecompressFrame(const unsigned char *src, size_t srcLen, std::string &out) {
    unsigned long long cs = ZSTD_getFrameContentSize(src, srcLen);
    if (cs == ZSTD_CONTENTSIZE_ERROR) return false;
    size_t cap = (cs == ZSTD_CONTENTSIZE_UNKNOWN) ? (1u << 18) : (size_t)cs + 16;
    for (int tries = 0; tries < 10; ++tries) {
        std::vector<char> dst(cap);
        size_t got = ZSTD_decompress(dst.data(), cap, src, srcLen);
        if (!ZSTD_isError(got)) { out.assign(dst.data(), got); return true; }
        if (ZSTD_getErrorCode(got) != ZSTD_error_dstSize_tooSmall) return false;
        cap *= 4;
    }
    return false;
}

static bool DecompressStreamAll(const std::vector<unsigned char> &src, std::string &out) {
    ZSTD_DCtx *dctx = ZSTD_createDCtx();
    if (!dctx) return false;
    ZSTD_inBuffer in{src.data(), src.size(), 0};
    std::vector<char> buf(1 << 16);
    bool any = false;
    while (in.pos < in.size) {
        ZSTD_outBuffer o{buf.data(), buf.size(), 0};
        size_t r = ZSTD_decompressStream(dctx, &o, &in);
        if (ZSTD_isError(r)) break;
        if (o.pos) { out.append(buf.data(), o.pos); any = true; }
        if (r == 0) break;
    }
    ZSTD_freeDCtx(dctx);
    return any;
}

static std::string Unpack(const std::vector<unsigned char> &data) {
    std::string out;
    if (data.empty()) return out;
    size_t n = data.size(), pos = 0;
    while (pos < n) {
        size_t nxt = n;
        for (size_t i = pos + 1; i + 3 < n; ++i) if (IsMagic(data, i)) { nxt = i; break; }
        std::string piece;
        if (DecompressFrame(data.data() + pos, nxt - pos, piece)) {
            out += piece;
        } else {
            if (out.empty()) { DecompressStreamAll(data, out); return out; }
            std::vector<unsigned char> tail(data.begin() + pos, data.end());
            DecompressStreamAll(tail, out);
            return out;
        }
        if (nxt == n) break;
        pos = nxt;
    }
    return out;
}

static bool ReadWholeFile(const std::string &path, std::vector<unsigned char> &out) {
    std::ifstream f(path, std::ios::binary);
    if (!f) return false;
    f.seekg(0, std::ios::end);
    std::streamoff len = f.tellg();
    if (len <= 0) { out.clear(); return len == 0; }
    f.seekg(0, std::ios::beg);
    out.resize((size_t)len);
    f.read(reinterpret_cast<char *>(out.data()), len);
    out.resize((size_t)f.gcount());
    return f.gcount() > 0;
}

// ---------------------------------------------------------------- 平台
// 必须带小数秒：Python 用的是 st.st_mtime（浮点），而 _stat64 的 st_mtime 是整秒。
// 差的零点几秒会一路进到 age_sec，两边永远对不齐 —— 所以直接读 FILETIME。
static double FileMtime(const std::string &path) {
#if defined(_WIN32)
    HANDLE h = CreateFileA(path.c_str(), GENERIC_READ,
                           FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                           nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (h == INVALID_HANDLE_VALUE) return 0.0;
    FILETIME ct{}, at{}, wt{};
    BOOL ok = GetFileTime(h, &ct, &at, &wt);
    CloseHandle(h);
    if (!ok) return 0.0;
    // FILETIME 是 1601-01-01 起的 100ns 计数，换到 unix epoch 的浮点秒
    ULARGE_INTEGER u; u.LowPart = wt.dwLowDateTime; u.HighPart = wt.dwHighDateTime;
    return (double)u.QuadPart / 1e7 - 11644473600.0;
#else
    struct stat st{};
    if (stat(path.c_str(), &st) != 0) return 0.0;
    return (double)st.st_mtime;
#endif
}

static std::string BaseName(const std::string &p) {
    std::string s = p;
    while (s.size() > 1 && (s.back() == '\\' || s.back() == '/')) s.pop_back();
    size_t i = s.find_last_of("\\/");
    return i == std::string::npos ? s : s.substr(i + 1);
}

static std::string SessionKey(const std::string &path) {
    std::vector<std::string> parts;
    size_t start = 0;
    for (size_t i = 0; i <= path.size(); ++i) {
        if (i == path.size() || path[i] == '\\' || path[i] == '/') {
            if (i > start) parts.push_back(path.substr(start, i - start));
            start = i + 1;
        }
    }
    return parts.size() >= 2 ? parts[parts.size() - 2] : path;
}

// Python 的 fallback 是 path.split(os.sep)[-3][:28]：按字符切，这里同口径
static std::string ProjectFallback(const std::string &path) {
    std::vector<std::string> parts;
    size_t start = 0;
    for (size_t i = 0; i <= path.size(); ++i) {
        if (i == path.size() || path[i] == '\\' || path[i] == '/') {
            if (i > start) parts.push_back(path.substr(start, i - start));
            start = i + 1;
        }
    }
    if (parts.size() < 3) return parts.empty() ? path : Utf8Trunc(parts.front(), 28);
    return Utf8Trunc(parts[parts.size() - 3], 28);
}

static std::string ProjectName(const std::string &cwd, const std::string &fallback) {
    if (cwd.empty()) return fallback;
    std::string n = BaseName(cwd);
    return n.empty() ? fallback : n;
}

static std::string ExpandUser(const std::string &p) {
    if (p.size() < 2 || p[0] != '~' || (p[1] != '/' && p[1] != '\\')) return p;
    const char *home = getenv("USERPROFILE");
    if (!home) home = getenv("HOME");
    std::string rest = p.substr(2);
    return std::string(home ? home : "") + (rest.empty() ? "" : "\\" + rest);
}

static bool HarnessRunning() {
#if defined(_WIN32)
    HANDLE snap = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
    if (snap == INVALID_HANDLE_VALUE) return false;
    PROCESSENTRY32W e{};
    e.dwSize = sizeof(e);
    bool found = false;
    if (Process32FirstW(snap, &e)) {
        do {
            std::string name;
            for (int i = 0; e.szExeFile[i]; ++i) {
                wchar_t c = (wchar_t)towlower(e.szExeFile[i]);
                name += (char)(c < 0x80 ? c : '?');
            }
            for (const char *a : kAppExes) if (name == a) { found = true; break; }
            if (found) break;
        } while (Process32NextW(snap, &e));
    }
    CloseHandle(snap);
    return found;
#else
    return false;
#endif
}

static void WalkSessions(const std::string &dir, int depth, std::vector<std::string> &out) {
#if defined(_WIN32)
    // 会话在 `<root>/sessions/*/*/session.v4.jsonl.zstd`，也就是 sessions 之下还要
    // 走两层目录、在第三层里列文件。depth 是"当前正在列的这一层的序号"，
    // 所以放行到 3：写成 >2 会在第三层之前就 return，扫出来永远是 0 个
    // （对照器第一次跑就是这么抓到 snapshot 全空的）。
    if (depth > 3) return;
    WIN32_FIND_DATAA fd{};
    HANDLE h = FindFirstFileA((dir + "\\*").c_str(), &fd);
    if (h == INVALID_HANDLE_VALUE) return;
    do {
        std::string name = fd.cFileName;
        if (name == "." || name == "..") continue;
        std::string full = dir + "\\" + name;
        if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) WalkSessions(full, depth + 1, out);
        else if (name == kSessionFile) out.push_back(full);
    } while (FindNextFileA(h, &fd));
    FindClose(h);
#else
    (void)dir; (void)depth; (void)out;
#endif
}

// ---------------------------------------------------------------- 会话与判定
using Events = std::vector<JValue>;

static const JValue *Data(const JValue &e) { return e.at("data"); }
static std::string TypeOf(const JValue &e) {
    const JValue *t = e.at("type");
    return t ? t->asStr() : std::string();
}

struct Session {
    Events tail;
    std::unique_ptr<JValue> lastTodo;
    std::unique_ptr<JValue> lastTurnEnd;
    std::string cwd, title;
    long records = 0;
    long long usageIn = 0, usageOut = 0, usageTotal = 0;
};

static Session ReadSessionFromPlain(const std::string &plain) {
    Session s;
    s.tail.reserve(1024);
    size_t start = 0, n = plain.size();
    while (start < n) {
        size_t end = plain.find('\n', start);
        std::string line = (end == std::string::npos) ? plain.substr(start)
                                                      : plain.substr(start, end - start);
        start = (end == std::string::npos) ? n : end + 1;
        if (line.find_first_not_of(" \t\r") == std::string::npos) continue;
        JValue v;
        if (!json::Parse(line, v) || v.kind != JValue::Obj) continue;
        ++s.records;
        std::string type = TypeOf(v);
        const JValue *d = v.at("data");
        if (type == "session") {
            // cwd 挂在 session 记录的**根**上（Python 是 ev.get("cwd")），不在 data 里。
            // 按 data.cwd 取会永远取空，project 就退化成"目录编码名"，
            // 整条状态栏显示的项目名跟着错 —— 快照对照器第一次跑就是这么抓出来的。
            const JValue *c = v.at("cwd");
            if (c && c->kind == JValue::Str) s.cwd = c->asStr();
        } else if (type == "session/title") {
            const JValue *t = d ? d->at("title") : nullptr;
            if (t && t->kind == JValue::Str && !t->str.empty()) s.title = t->asStr();
        } else if (type == "todo/write") {
            const JValue *todos = d ? d->at("todos") : nullptr;
            if (todos) s.lastTodo = std::make_unique<JValue>(*todos);
        } else if (type == "turn/end") {
            s.lastTurnEnd = std::make_unique<JValue>(v);
        } else if (type == "assistant/attempt") {
            const JValue *stream = d ? d->at("stream") : nullptr;
            if (stream && stream->kind == JValue::Arr) {
                for (const auto &item : stream->arr) {
                    const JValue *chunk = item.at("chunk");
                    const JValue *u = chunk ? chunk->at("usage") : nullptr;
                    if (!u) continue;
                    const JValue *a = u->at("inputTokens"), *b = u->at("outputTokens"),
                               *c = u->at("totalTokens");
                    if (a && a->kind == JValue::Num) s.usageIn += (long long)a->num;
                    if (b && b->kind == JValue::Num) s.usageOut += (long long)b->num;
                    if (c && c->kind == JValue::Num) s.usageTotal += (long long)c->num;
                }
            }
        }
        if (s.tail.size() >= kTailEvents) s.tail.erase(s.tail.begin());
        s.tail.push_back(std::move(v));
    }
    return s;
}

static std::vector<const JValue *> ContentBlocks(const JValue &e) {
    std::vector<const JValue *> out;
    const JValue *d = e.at("data");
    const JValue *m = d ? d->at("message") : nullptr;
    const JValue *c = m ? m->at("content") : nullptr;
    if (c && c->kind == JValue::Arr) for (const auto &b : c->arr) out.push_back(&b);
    return out;
}

struct Pending {
    std::string kind, tool, text;
    std::vector<std::string> options;
    bool ok = false;
};

static Pending ApprovalState(const Events &ev) {
    std::vector<std::pair<std::string, const JValue *>> asked;
    for (const auto &e : ev) {
        const JValue *d = Data(e);
        const JValue *id = d ? d->at("id") : nullptr;
        if (!d || !id || id->kind != JValue::Str) continue;
        std::string t = TypeOf(e);
        if (t == "approval/asked") asked.emplace_back(id->asStr(), d);
        else if (t == "approval/decided") {
            for (size_t i = 0; i < asked.size(); ++i)
                if (asked[i].first == id->asStr()) { asked.erase(asked.begin() + i); break; }
        }
    }
    if (asked.empty()) return {};
    const JValue *p = asked.front().second;
    Pending r;
    r.ok = true; r.kind = "approval";
    const JValue *tn = p->at("toolName");
    if (tn && tn->kind == JValue::Str) r.tool = tn->asStr();
    const JValue *reason = p->at("reason");
    if (reason && reason->kind == JValue::Str && !reason->str.empty()) r.text = reason->str;
    else r.text = r.tool + " 需要授权";
    return r;
}

static std::vector<std::string> CallIds(const Events &ev, const std::string &wanted) {
    std::vector<std::string> out;
    for (const auto &e : ev) {
        if (TypeOf(e) != wanted) continue;
        const JValue *d = Data(e);
        const JValue *c = d ? d->at("callId") : nullptr;
        if (c && c->kind == JValue::Str) out.push_back(c->asStr());
    }
    return out;
}

// Python 对非字符串元素用 str(o) 而不是过滤掉（少一个可点选项就是丢信息），
// 这里同口径：数字/布尔也 stringify 出来。
static std::string Stringify(const JValue &v) {
    if (v.kind == JValue::Str) return v.str;
    if (v.kind == JValue::Num) { std::string o; json::EmitNum(o, v.num); return o; }
    if (v.kind == JValue::Bool) return v.b ? "True" : "False";
    return "None";
}

static Pending QuestionState(const Events &ev) {
    auto resolved = CallIds(ev, "tool/result");
    for (auto it = ev.rbegin(); it != ev.rend(); ++it) {
        if (TypeOf(*it) != "tool/call") continue;
        const JValue *d = Data(*it);
        if (!d) continue;
        std::string name = d->at("name") ? d->at("name")->asStr() : "";
        auto blk = kBlockingTools.find(name);
        if (blk == kBlockingTools.end()) continue;
        std::string callId = d->at("callId") ? d->at("callId")->asStr() : "";
        if (!callId.empty() && std::find(resolved.begin(), resolved.end(), callId) != resolved.end())
            continue;

        JValue args;
        const JValue *raw = d->at("arguments");
        if (raw && raw->kind == JValue::Str) json::Parse(raw->str, args);

        Pending r; r.ok = true; r.kind = "question"; r.tool = name;
        const JValue *qs = args.kind == JValue::Obj ? args.at("questions") : nullptr;
        if (name == "ask_user_question" && qs && qs->kind == JValue::Arr && !qs->arr.empty()) {
            const JValue &first = qs->arr.front();
            if (first.at("question")) r.text = first.at("question")->asStr();
            if (qs->arr.size() > 1) r.text += "（共 " + std::to_string(qs->arr.size()) + " 问）";
            const JValue *opts = first.at("options");
            if (opts && opts->kind == JValue::Arr)
                for (const auto &o : opts->arr)
                    if (o.kind == JValue::Obj && o.at("label")) r.options.push_back(Stringify(*o.at("label")));
        } else if (args.kind == JValue::Obj && args.at("question")) {
            r.text = args.at("question")->asStr();
        }
        if (r.text.empty()) r.text = CollapseSpaces("模型在等待你" + blk->second);
        return r;
    }
    return {};
}

static bool TurnOpen(const Events &ev) {
    long lastStart = -1, lastEnd = -1;
    for (size_t i = 0; i < ev.size(); ++i) {
        std::string t = TypeOf(ev[i]);
        if (t == "turn/start") lastStart = (long)i;
        else if (t == "turn/end") lastEnd = (long)i;
    }
    return lastStart > lastEnd;
}

static std::string ReasonKind(const JValue *turnEnd) {
    if (!turnEnd) return "";
    const JValue *d = turnEnd->at("data");
    const JValue *r = d ? d->at("reason") : nullptr;
    if (!r) return "";
    if (r->kind == JValue::Obj) { const JValue *k = r->at("kind"); return k ? k->asStr() : ""; }
    if (r->kind == JValue::Str) return r->asStr();
    return "";
}

struct Todo { int total = 0, done = 0, inProgress = 0, pending = 0; bool ok = false; };

static Todo TodoProgress(const JValue *todos) {
    Todo t;
    if (!todos || todos->kind != JValue::Arr || todos->arr.empty()) return t;
    t.ok = true;
    t.total = (int)todos->arr.size();
    for (const auto &e : todos->arr) {
        std::string s = (e.kind == JValue::Obj && e.at("status")) ? e.at("status")->asStr() : "";
        for (auto &c : s) c = (char)tolower((unsigned char)c);
        if (s == "completed" || s == "complete" || s == "done") ++t.done;
        else if (s == "in_progress" || s == "working" || s == "active") ++t.inProgress;
        else ++t.pending;
    }
    return t;
}

struct Verdict {
    std::string state = "idle";
    double age = 0;
    bool hasTurn = false, hasStep = false;
    long turn = 0, step = 0;
    std::string lastEvent, lastTool, endReason, error;
    Pending pending;
};

static Verdict Classify(const Session &s, double mtime, double ts) {
    const Events &ev = s.tail;
    Verdict v;
    v.age = ts - mtime; if (v.age < 0) v.age = 0;
    v.endReason = ReasonKind(s.lastTurnEnd.get());
    if (!ev.empty()) v.lastEvent = TypeOf(ev.back());

    for (auto it = ev.rbegin(); it != ev.rend(); ++it) {
        const JValue *d = Data(*it);
        if (!d) continue;
        if (!v.hasTurn && d->at("turn")) { v.turn = (long)d->at("turn")->num; v.hasTurn = true; }
        if (!v.hasStep && d->at("step")) { v.step = (long)d->at("step")->num; v.hasStep = true; }
        if (v.lastTool.empty() && TypeOf(*it) == "tool/call" && d->at("name"))
            v.lastTool = d->at("name")->asStr();
        if (v.hasTurn && v.hasStep && !v.lastTool.empty()) break;
    }

    Pending pend = ApprovalState(ev);
    if (!pend.ok) pend = QuestionState(ev);
    bool open = TurnOpen(ev);

    if (pend.ok && open) { v.state = "needs_action"; v.pending = pend; return v; }
    if (ev.empty()) { v.state = "idle"; return v; }

    if (open) {
        if (pend.ok) { v.state = "needs_action"; v.pending = pend; }
        else if (v.age > kStallWindow) v.state = "stalled";
        else if (v.lastEvent == "assistant/message") {
            bool toolCall = false, hasText = false;
            for (const JValue *b : ContentBlocks(ev.back())) {
                std::string t = b->at("type") ? b->at("type")->asStr() : "";
                if (t == "tool-call" || t == "input_json_delta") toolCall = true;
                else if (t == "text") hasText = true;
            }
            v.state = toolCall ? "tool_running" : (hasText ? "answering" : "thinking");
        }
        else if (v.lastEvent == "tool/call") v.state = "tool_running";
        else v.state = "thinking";
        return v;
    }

    if (v.endReason == "blocked") {
        v.state = "needs_action";
        Pending q = QuestionState(ev);
        if (q.ok) v.pending = q;
        else { v.pending.ok = true; v.pending.kind = "blocked"; v.pending.text = "模型已停下，等待你的指示"; }
    } else if (v.endReason == "error") {
        v.state = "error";
        const JValue *d = s.lastTurnEnd ? s.lastTurnEnd->at("data") : nullptr;
        const JValue *r = d ? d->at("reason") : nullptr;
        const JValue *err = r ? r->at("error") : nullptr;
        const JValue *msg = err ? err->at("message") : nullptr;
        if (msg && msg->kind == JValue::Str) v.error = Utf8Trunc(msg->asStr(), 180);
    } else if (v.endReason == "aborted") v.state = "aborted";
    else if (v.endReason == "completed") v.state = (v.age <= kDoneWindow) ? "done" : "idle";
    else v.state = "idle";
    return v;
}

// ---------------------------------------------------------------- 解析缓存
// 照 read_session_cached：按 (大小, mtime) 复用解析结果，LRU 逐个淘汰。
// 上限 200 与 _CACHE_MAX 同值 —— 那边注释里记着教训：会话数一超上限就用 clear()
// 的写法等于**永远不命中**，而这恰恰是最需要它的时候（本机 59 个会话时单轮 1567ms）。
//
// 命中数每轮打到 stderr 的 CACHE 行：常驻引擎"这一轮到底重解了几个文件"本来就是
// 排障要看的东西，不是只为测试才有的开关。
struct CacheEntry {
    std::string path;
    long long size = 0;
    double mtime = 0;
    std::shared_ptr<Session> sess;
};
static std::vector<CacheEntry> gCache;
static const size_t kCacheMax = 200;

static long long FileSize(const std::string &path) {
#if defined(_WIN32)
    WIN32_FILE_ATTRIBUTE_DATA d{};
    if (!GetFileAttributesExA(path.c_str(), GetFileExInfoStandard, &d)) return -1;
    ULARGE_INTEGER u; u.LowPart = d.nFileSizeLow; u.HighPart = d.nFileSizeHigh;
    return (long long)u.QuadPart;
#else
    struct stat st{};
    if (stat(path.c_str(), &st) != 0) return -1;
    return (long long)st.st_size;
#endif
}

static std::shared_ptr<Session> ReadSessionCached(const std::string &path, double mtime,
                                                  long long size, long long &hits) {
    for (size_t i = 0; i < gCache.size(); ++i) {
        if (gCache[i].path == path && gCache[i].size == size && gCache[i].mtime == mtime) {
            ++hits;
            auto e = gCache[i];
            gCache.erase(gCache.begin() + i);
            gCache.push_back(e);                       // move_to_end
            return e.sess;
        }
    }
    std::vector<unsigned char> raw;
    auto s = std::make_shared<Session>();
    if (ReadWholeFile(path, raw)) *s = ReadSessionFromPlain(Unpack(raw));
    gCache.push_back({path, size, mtime, s});
    while (gCache.size() > kCacheMax) gCache.erase(gCache.begin());
    return s;
}

// ---------------------------------------------------------------- 一行会话
struct Row {
    Verdict v;
    std::string path, key, project, cwd, title;
    int priority = 0;
    long records = 0;
    bool hasRecords = false;
    long long usageIn = 0, usageOut = 0, usageTotal = 0;
    Todo todo;
};

static std::vector<Row> Scan(const std::string &globRoot, double ts, long long &hits) {
    std::vector<std::string> files;
    WalkSessions(globRoot, 1, files);
    std::vector<std::pair<double, std::string>> stamped;
    for (const auto &f : files) {
        double m = FileMtime(f);
        if (m <= 0) continue;
        stamped.emplace_back(m, f);
    }
    std::sort(stamped.begin(), stamped.end(),
              [](const std::pair<double, std::string> &a, const std::pair<double, std::string> &b) {
                  return a.first > b.first;                        // mtime 倒序
              });

    // 文件已经消失的缓存条目先丢掉（Python 的 scan 里同一段）
    std::vector<std::string> alive;
    for (const auto &it : stamped) alive.push_back(it.second);
    for (size_t i = 0; i < gCache.size();) {
        bool keep = false;
        for (const auto &a : alive) if (a == gCache[i].path) { keep = true; break; }
        if (keep) ++i; else gCache.erase(gCache.begin() + i);
    }

    std::vector<Row> out;
    for (const auto &it : stamped) {
        long long size = FileSize(it.second);
        std::shared_ptr<Session> sp = ReadSessionCached(it.second, it.first, size, hits);
        Row r;
        r.path = it.second;
        r.v = Classify(*sp, it.first, ts);
        r.key = SessionKey(it.second);
        r.cwd = sp->cwd;
        r.project = ProjectName(sp->cwd, ProjectFallback(it.second));
        r.title = sp->title;
        r.records = sp->records;
        r.hasRecords = true;
        r.usageIn = sp->usageIn; r.usageOut = sp->usageOut; r.usageTotal = sp->usageTotal;
        r.todo = TodoProgress(sp->lastTodo.get());
        r.priority = StateOf(r.v.state)->priority;
        out.push_back(std::move(r));
    }
    return out;
}


// 主状态取"最近在干活的那个"；等你处理的单独列出来。照 pick_primary 一比一。
static void PickPrimary(const std::vector<Row> &rows, const Row *&primary,
                        std::vector<const Row *> &waiting) {
    for (const auto &s : rows)
        if (s.v.state == "needs_action" && s.v.age <= kWaitPromoteWindow) waiting.push_back(&s);
    std::vector<const Row *> recent;
    for (const auto &s : rows) if (s.v.age <= kActiveWindow) recent.push_back(&s);

    const std::vector<const Row *> &pool = !recent.empty() ? recent : waiting;
    if (pool.empty()) { primary = rows.empty() ? nullptr : &rows.front(); return; }

    std::vector<const Row *> ranked = pool;
    std::stable_sort(ranked.begin(), ranked.end(),
                     [](const Row *a, const Row *b) {
                         if (a->priority != b->priority) return a->priority > b->priority;
                         return a->v.age < b->v.age;
                     });
    primary = ranked.front();
    if (!waiting.empty()) return;                       // 有等待的就用它，否则退回 busy
    std::vector<const Row *> busy;
    int busyFloor = StateOf("tool_running")->priority;
    for (size_t i = 1; i < ranked.size(); ++i)
        if (ranked[i]->priority >= busyFloor || ranked[i]->v.state == "needs_action")
            busy.push_back(ranked[i]);
    waiting = busy;
}

// ---------------------------------------------------------------- 文本组装
static std::string Money(const std::string &available, const std::string &currency,
                         const std::string &total) {
    if (available != "1") return "--";
    std::string sym = currency == "USD" ? "$" : (currency == "CNY" ? "¥" : "");
    return sym + (total.empty() ? "0" : total);
}

static std::string Thousands(long long v) {
    std::string d = std::to_string(v < 0 ? -v : v), r;
    for (size_t i = 0; i < d.size(); ++i) { if (i && (d.size() - i) % 3 == 0) r += ','; r += d[i]; }
    return (v < 0 ? "-" : "") + r;
}

struct Text {
    std::string label, glyph, color, detail, strip, stripLeft, stripRight, tooltip, tooltipFull;
    std::vector<std::string> tipLines;
};

static Text ComposeText(const Row *primary, const std::string &state, const std::string &money,
                        size_t waitingCount) {
    Text t;
    const StateInfo *si = StateOf(state);
    t.label = si->label; t.glyph = si->glyph; t.color = si->color; t.stripRight = money;
    if (primary) {
        std::string left = t.label + " · " + primary->project;
        if (primary->v.hasTurn) {
            left += " · T" + std::to_string(primary->v.turn);
            if (primary->v.hasStep) left += "/S" + std::to_string(primary->v.step);
        }
        t.stripLeft = left;
        t.detail = left + " · " + money;
        std::string tip = t.label + "｜" + primary->project;
        if (primary->v.hasTurn) tip += "｜T" + std::to_string(primary->v.turn);
        if (primary->v.pending.ok) tip += "｜" + primary->v.pending.text;
        tip += "｜静默 " + std::to_string((long long)primary->v.age) + "s";
        t.tooltip = ClampTip(tip);

        t.tipLines.push_back(t.label + "｜" + primary->project);
        if (!primary->title.empty()) t.tipLines.push_back("会话：" + Utf8Trunc(primary->title, 60));
        if (primary->v.pending.ok) {
            t.tipLines.push_back("等你处理：" + Utf8Trunc(primary->v.pending.text, 120));
            if (!primary->v.pending.options.empty()) {
                std::string o = "选项：";
                for (size_t i = 0; i < primary->v.pending.options.size(); ++i) {
                    if (i) o += " / ";
                    o += primary->v.pending.options[i];
                }
                t.tipLines.push_back(o);
            }
        }
        if (!primary->v.lastTool.empty() && (state == "tool_running" || state == "thinking"))
            t.tipLines.push_back("最近工具：" + primary->v.lastTool);
        if (primary->todo.ok) {
            std::string s = "任务：" + std::to_string(primary->todo.done) + "/" +
                            std::to_string(primary->todo.total) + " 完成";
            if (primary->todo.inProgress) s += "，" + std::to_string(primary->todo.inProgress) + " 进行中";
            t.tipLines.push_back(s);
        }
        if (primary->usageTotal)
            t.tipLines.push_back("用量：入 " + Thousands(primary->usageIn) + " / 出 " +
                                 Thousands(primary->usageOut) + " tokens");
        t.tipLines.push_back("已静默 " + std::to_string((long long)primary->v.age) + "s ｜ 待处理 " +
                             std::to_string(waitingCount) + " 个");
    } else {
        t.stripLeft = t.label;
        t.detail = t.label + " · " + money;
        t.tooltip = ClampTip(t.label + "｜余额 " + money);
        t.tipLines = {t.label, "余额 " + money, "没有找到 dsh 会话记录"};
    }
    t.strip = t.detail;
    for (size_t i = 0; i < t.tipLines.size(); ++i) { if (i) t.tooltipFull += "\n"; t.tooltipFull += t.tipLines[i]; }
    return t;
}

// ---------------------------------------------------------------- JSON 出
struct W {
    std::string o;
    bool first = true;
    void key(const std::string &k) { if (!first) o += ','; first = false; json::EmitStr(o, k); o += ':'; }
    void str(const std::string &k, const std::string &v) { key(k); json::EmitStr(o, v); }
    void nul(const std::string &k) { key(k); o += "null"; }
    void optStr(const std::string &k, const std::string &v) { v.empty() ? nul(k) : str(k, v); }
    // 整数走原样出、浮点走 EmitNum。写成模板而不是 (double)/(long long) 两个重载：
    // 两个重载传 int 会歧义（C2668），而这格大部分字段都是 int。
    template <class T>
    typename std::enable_if<std::is_arithmetic<T>::value, void>::type
    num(const std::string &k, T v) {
        key(k);
        if (std::is_floating_point<T>::value) json::EmitNum(o, (double)v);
        else o += std::to_string((long long)v);
    }
    void boolean(const std::string &k, bool v) { key(k); o += v ? "true" : "false"; }
    void begin(const std::string &k) { key(k); o += '{'; first = true; }
    void beginArr(const std::string &k) { key(k); o += '['; }
    void end() { o += '}'; first = false; }
};

static void EmitVerdict(W &w, const Verdict &v) {
    w.str("state", v.state);
    { char buf[32]; snprintf(buf, sizeof buf, "%.1f", v.age); w.key("age_sec"); w.o += buf; }
    w.optStr("last_event", v.lastEvent);
    w.optStr("last_tool", v.lastTool);
    w.str("end_reason", v.endReason);          // 恒为字符串：Python 给的是 ""，不是 null
    if (v.hasTurn) w.num("turn", v.turn); else w.nul("turn");
    if (v.hasStep) w.num("step", v.step); else w.nul("step");
    if (v.pending.ok) {
        w.begin("pending");
        w.str("kind", v.pending.kind);
        w.optStr("tool", v.pending.tool);
        w.str("text", v.pending.text);
        w.key("options"); w.o += '[';
        for (size_t i = 0; i < v.pending.options.size(); ++i) { if (i) w.o += ','; json::EmitStr(w.o, v.pending.options[i]); }
        w.o += ']';
        w.end();
    } else w.nul("pending");
    if (!v.error.empty()) { w.key("error"); json::EmitStr(w.o, v.error); }
}

static void EmitSessionRow(W &w, const Row &r) {
    w.key("key"); json::EmitStr(w.o, r.key);
    w.key("project"); json::EmitStr(w.o, r.project);
    if (r.title.empty()) w.nul("title");
    else { w.key("title"); json::EmitStr(w.o, Utf8Trunc(r.title, 80)); }
    w.key("state"); json::EmitStr(w.o, r.v.state);
    if (r.v.hasTurn) { w.key("turn"); w.o += std::to_string(r.v.turn); } else w.nul("turn");
    if (r.v.hasStep) { w.key("step"); w.o += std::to_string(r.v.step); } else w.nul("step");
    { char buf[32]; snprintf(buf, sizeof buf, "%.1f", r.v.age); w.key("age_sec"); w.o += buf; }
    w.optStr("last_event", r.v.lastEvent);
    w.optStr("last_tool", r.v.lastTool);
    w.key("end_reason"); json::EmitStr(w.o, r.v.endReason);
    if (r.hasRecords) { w.key("records"); w.o += std::to_string(r.records); } else w.nul("records");
    if (r.todo.ok) {
        w.begin("todo");
        w.num("total", r.todo.total); w.num("done", r.todo.done);
        w.num("in_progress", r.todo.inProgress); w.num("pending", r.todo.pending);
        w.end();
    } else w.nul("todo");
    w.begin("usage_total");
    w.num("input", r.usageIn); w.num("output", r.usageOut); w.num("total", r.usageTotal);
    w.end();
    if (r.v.pending.ok) {
        w.begin("pending");
        w.str("kind", r.v.pending.kind);
        w.optStr("tool", r.v.pending.tool);
        w.str("text", Utf8Trunc(r.v.pending.text, 120));
        w.key("options"); w.o += '[';
        for (size_t i = 0; i < r.v.pending.options.size(); ++i) { if (i) w.o += ','; json::EmitStr(w.o, r.v.pending.options[i]); }
        w.o += ']';
        w.end();
    } else w.nul("pending");
}

// snapshot 的 session 那一格：字段白名单照 Python 的 dict 推导逐项搬
// （project / cwd / title / state / turn / step / age_sec / last_event / last_tool /
//  pending / end_reason / error / todo / usage_total / key / records）。
// 顺序不影响 C# 反序列化（按名字取），但**少一格**就是面板某处永远空白。
static void EmitSessionDict(W &w, const Row &r) {
    w.str("project", r.project);
    w.optStr("cwd", r.cwd);
    w.optStr("title", r.title);
    w.str("state", r.v.state);
    if (r.v.hasTurn) w.num("turn", r.v.turn); else w.nul("turn");
    if (r.v.hasStep) w.num("step", r.v.step); else w.nul("step");
    { char buf[32]; snprintf(buf, sizeof buf, "%.1f", r.v.age); w.key("age_sec"); w.o += buf; }
    w.optStr("last_event", r.v.lastEvent);
    w.optStr("last_tool", r.v.lastTool);
    if (r.v.pending.ok) {
        w.begin("pending");
        w.str("kind", r.v.pending.kind);
        w.optStr("tool", r.v.pending.tool);
        w.str("text", r.v.pending.text);
        w.key("options"); w.o += '[';
        for (size_t i = 0; i < r.v.pending.options.size(); ++i) { if (i) w.o += ','; json::EmitStr(w.o, r.v.pending.options[i]); }
        w.o += ']';
        w.end();
    } else w.nul("pending");
    w.str("end_reason", r.v.endReason);
    // Python 只在 error 那一支才往 out 里塞 "error"，别的状态根本没这一格
    if (!r.v.error.empty()) { w.key("error"); json::EmitStr(w.o, r.v.error); }
    if (r.todo.ok) {
        w.begin("todo");
        w.num("total", r.todo.total); w.num("done", r.todo.done);
        w.num("in_progress", r.todo.inProgress); w.num("pending", r.todo.pending);
        w.end();
    } else w.nul("todo");
    w.begin("usage_total");
    w.num("input", r.usageIn); w.num("output", r.usageOut); w.num("total", r.usageTotal);
    w.end();
    w.str("key", r.key);
    if (r.hasRecords) w.num("records", r.records); else w.nul("records");
}

// ---------------------------------------------------------------- 余额
// 这一层的全部风险都在**互操作**上：C++ 存的 Key 必须 Python 读得回来，反之亦然
// （切换期两边会并存；万一只有单边能读，表现是"保存成功但来源永远 none"，
//  而保存那一步照样退出 0 —— 今天刚在 Python 侧修掉一个同型的不对称，见 15d003b）。
// 所以 DPAPI 的参数逐项照 dsh_state.py 的 _dpapi：描述符 "Vigil"、无附加熵、
// 加密时 CRYPTPROTECT_UI_FORBIDDEN，解密时 flags 给 0。
#if defined(_WIN32)
#include <wincrypt.h>
#include <winhttp.h>
#endif

static std::string StateDir() {
    const char *la = getenv("LOCALAPPDATA");
    std::string base = (la && *la) ? std::string(la)
                                   : (getenv("USERPROFILE") ? std::string(getenv("USERPROFILE")) + "\\AppData\\Local" : std::string("."));
    return base + "\\Vigil";
}

static std::string BalancePath(const char *name) { return StateDir() + "\\" + name; }

static bool Dpapi(const std::vector<unsigned char> &in, bool protect,
                  std::vector<unsigned char> &out) {
#if defined(_WIN32)
    DATA_BLOB bi{}; bi.cbData = (DWORD)in.size(); bi.pbData = (BYTE *)in.data();
    DATA_BLOB bo{};
    BOOL ok = protect
        ? CryptProtectData(&bi, L"Vigil", nullptr, nullptr, nullptr, CRYPTPROTECT_UI_FORBIDDEN, &bo)
        : CryptUnprotectData(&bi, nullptr, nullptr, nullptr, nullptr, 0, &bo);
    if (!ok) return false;
    out.assign(bo.pbData, bo.pbData + bo.cbData);
    LocalFree(bo.pbData);
    return true;
#else
    (void)in; (void)protect; (void)out;
    return false;
#endif
}

static std::string ReadTextFile(const std::string &path) {
    std::vector<unsigned char> raw;
    if (!ReadWholeFile(path, raw)) return "";
    return std::string(raw.begin(), raw.end());
}

// Key 的落点。返回值是"来源"字符串，面板只显示它、不显示 Key 本身。
struct KeyInfo { std::string key, source; };

static KeyInfo BalanceKey() {
    for (const char *var : {"DEEPSEEK_BALANCE_KEY", "DEEPSEEK_API_KEY"}) {
        const char *v = getenv(var);
        if (v && *v) {
            std::string s = v;
            while (!s.empty() && (s.back() == ' ' || s.back() == '\n' || s.back() == '\r')) s.pop_back();
            size_t f = s.find_first_not_of(" \t\r\n");
            if (f != std::string::npos) return {s.substr(f), std::string("env:") + var};
        }
    }
    std::vector<unsigned char> blob;
    if (ReadWholeFile(BalancePath("balance.protected"), blob)) {
        std::vector<unsigned char> plain;
        if (Dpapi(blob, false, plain)) {
            std::string s(plain.begin(), plain.end());
            while (!s.empty() && (s.back() == '\0' || s.back() == '\n' || s.back() == '\r' || s.back() == ' ')) s.pop_back();
            if (!s.empty()) return {s, "dpapi"};
        }
    }
    // 明文候选：次序与 balance_key() 一致 —— state_dir() 排第一（DPAPI 失败时的
    // 回退就落在那儿），再是引擎目录与用户目录下的几处历史位置
    std::string home = getenv("USERPROFILE") ? getenv("USERPROFILE") : "";
    std::vector<std::string> files = {
        BalancePath("balance.key"),
        ".\\balance.key",
        "..\\balance.key",
    };
    if (!home.empty()) {
        files.push_back(home + "\\.dsh\\deepseek_balance_key");
        files.push_back(home + "\\.deepseek\\balance.key");
    }
    for (const auto &f : files) {
        std::string s = ReadTextFile(f);
        while (!s.empty() && (s.back() == '\n' || s.back() == '\r' || s.back() == ' ')) s.pop_back();
        if (s.empty()) continue;
        size_t slash = f.find_last_of("\\/");
        return {s, "file:" + (slash == std::string::npos ? f : f.substr(slash + 1))};
    }
    return {"", "none"};
}

static std::string SaveBalanceKey(const std::string &key) {
    std::vector<unsigned char> in(key.begin(), key.end());
    std::vector<unsigned char> out;
    if (Dpapi(in, true, out)) {
        std::string path = BalancePath("balance.protected");
        std::ofstream f(path, std::ios::binary | std::ios::trunc);
        f.write(reinterpret_cast<const char *>(out.data()), (std::streamsize)out.size());
        f.close();
        return path;
    }
    // DPAPI 不可用：与 Python 同一处理 —— 退回明文并**照样算保存成功**，
    // 所以路径里带这句提示，界面上不假装是加密的
    std::string path = BalancePath("balance.key");
    std::ofstream f(path, std::ios::trunc);
    f << key;
    f.close();
    return path + "（DPAPI 不可用，已存为明文，请注意文件权限）";
}

static bool ClearBalanceKey(std::string &removedPath) {
    for (const char *name : {"balance.protected", "balance.key"}) {
        std::string p = BalancePath(name);
        if (DeleteFileA(p.c_str())) { removedPath = p; return true; }
    }
    return false;
}

static double RefreshTokenMtime() { return FileMtime(StateDir() + "\\refresh.token"); }

struct Balance {
    bool available = false;
    std::string currency, total, granted, toppedUp, error, source;
    double fetchedAt = 0;
    bool cached = false;
    bool skipped = false;
};

static bool FetchJson(const std::string &key, std::string &body, std::string &err) {
#if defined(_WIN32)
    HINTERNET h = WinHttpOpen(L"Vigil", WINHTTP_ACCESS_TYPE_DEFAULT_PROXY,
                            WINHTTP_NO_PROXY_NAME, WINHTTP_NO_PROXY_BYPASS, 0);
    if (!h) { err = "WinHttpOpen 失败"; return false; }
    WinHttpSetTimeouts(h, 8000, 8000, 12000, 12000);
    bool ok = false;
    HINTERNET c = WinHttpConnect(h, L"api.deepseek.com", 443, 0);
    if (c) {
        std::string path = "/user/balance";
        std::wstring wpath;
        for (char ch : path) wpath += (wchar_t)(unsigned char)ch;
        HINTERNET r = WinHttpOpenRequest(c, L"GET", wpath.c_str(), nullptr, WINHTTP_NO_REFERER,
                                         WINHTTP_DEFAULT_ACCEPT_TYPES, WINHTTP_FLAG_SECURE);
        if (r) {
            std::wstring hdr = L"Authorization: Bearer ";
            for (unsigned char ch : key) hdr += (wchar_t)ch;
            hdr += L"\r\nAccept: application/json";
            if (WinHttpAddRequestHeaders(r, hdr.c_str(), (DWORD)-1, WINHTTP_ADDREQ_FLAG_ADD)) {
                if (WinHttpSendRequest(r, WINHTTP_NO_ADDITIONAL_HEADERS, 0,
                                       WINHTTP_NO_REQUEST_DATA, 0, 0, 0) &&
                    WinHttpReceiveResponse(r, nullptr)) {
                    DWORD status = 0, len = sizeof(status);
                    WinHttpQueryHeaders(r, WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER,
                                        WINHTTP_HEADER_NAME_BY_INDEX, &status, &len, WINHTTP_NO_HEADER_INDEX);
                    std::string got;
                    for (;;) {
                        DWORD avail = 0;
                        if (!WinHttpQueryDataAvailable(r, &avail) || !avail) break;
                        std::vector<char> chunk(avail);
                        DWORD read = 0;
                        if (!WinHttpReadData(r, chunk.data(), avail, &read) || !read) break;
                        got.append(chunk.data(), read);
                    }
                    if (status == 200) { body = got; ok = true; }
                    else {
                        std::string tail = got.substr(0, 200);
                        err = "HTTP " + std::to_string(status) +
                              (status == 401 ? "（Key 无效或没有余额查询权限，请到平台控制台 API Keys 页确认）" : "") +
                              " " + tail;
                        while (Utf8Len(err) > 220) err = err.substr(0, err.size() - 1);
                    }
                } else err = "HTTP 请求发送失败";
            } else err = "请求头写入失败";
            WinHttpCloseHandle(r);
        }
        WinHttpCloseHandle(c);
    } else err = "连接 api.deepseek.com 失败";
    WinHttpCloseHandle(h);
    return ok;
#else
    (void)key; (void)body; (void)err;
    return false;
#endif
}

// TTL 缓存 + refresh.token 强刷，照 fetch_balance 的语义
static Balance gBalanceCache;
static bool gHaveCache = false;
static double gCacheAt = 0;
static const double kBalanceTtl = 300.0;

static Balance FetchBalance(double ts, bool force) {
    KeyInfo ki = BalanceKey();
    if (!force && gHaveCache && (ts - gCacheAt) < kBalanceTtl) {
        Balance b = gBalanceCache;
        b.cached = true;
        return b;
    }
    Balance b;
    b.source = ki.source;
    b.fetchedAt = ts;
    if (ki.key.empty()) {
        b.error = "未配置余额 Key";
        gBalanceCache = b; gHaveCache = true; gCacheAt = ts;
        return b;
    }
    std::string body, err;
    if (!FetchJson(ki.key, body, err)) {
        b.error = err;
        gBalanceCache = b; gHaveCache = true; gCacheAt = ts;
        return b;
    }
    JValue root;
    if (!json::Parse(body, root) || root.kind != JValue::Obj) {
        b.error = "接口返回不是合法 JSON";
        gBalanceCache = b; gHaveCache = true; gCacheAt = ts;
        return b;
    }
    const JValue *infos = root.at("balance_infos");
    const JValue *first = (infos && infos->kind == JValue::Arr && !infos->arr.empty())
                              ? &infos->arr.front() : nullptr;
    auto strOf = [](const JValue *v) -> std::string {
        if (!v) return "";
        if (v->kind == JValue::Str) return v->str;
        return Stringify(*v);
    };
    const JValue *avail = root.at("is_available");
    b.available = (!avail || avail->b) && first != nullptr;
    if (first) {
        b.currency = strOf(first->at("currency"));
        b.total = strOf(first->at("total_balance"));
        b.granted = strOf(first->at("granted_balance"));
        b.toppedUp = strOf(first->at("topped_up_balance"));
    }
    if (b.currency.empty()) b.currency = strOf(root.at("currency"));
    if (b.total.empty()) {
        b.available = false;
        b.error = "接口未返回余额字段：" + Utf8Trunc(body, 160);
    }
    gBalanceCache = b; gHaveCache = true; gCacheAt = ts;
    return b;
}

static void EmitBalance(W &w, const Balance &b, bool wantBalance) {
    if (!wantBalance) {
        w.boolean("available", false);
        w.str("error", "已禁用");
        w.boolean("skipped", true);
        return;
    }
    w.boolean("available", b.available);
    if (b.currency.empty()) w.nul("currency"); else w.str("currency", b.currency);
    if (b.total.empty()) w.nul("total"); else w.str("total", b.total);
    if (b.granted.empty()) w.nul("granted"); else w.str("granted", b.granted);
    if (b.toppedUp.empty()) w.nul("topped_up"); else w.str("topped_up", b.toppedUp);
    if (b.error.empty()) w.nul("error"); else w.str("error", b.error);
    w.str("source", b.source);
    { char buf[32]; snprintf(buf, sizeof buf, "%.6f", b.fetchedAt); w.key("fetched_at"); w.o += buf; }
    w.boolean("cached", b.cached);
}

// ---------------------------------------------------------------- 整帧快照
static std::string Snapshot(double ts, bool wantBalance, long long *hitsOut = nullptr) {
    long long hits = 0;
    std::string home = getenv("USERPROFILE") ? getenv("USERPROFILE") : "";
    std::string root = home + "\\.dsh\\sessions";
    bool running = HarnessRunning();
    std::vector<Row> rows = Scan(root, ts, hits);
    // 每轮一行诊断：常驻时看"这轮重解了几个文件"，就能看出缓存到底有没有在工作
    std::fprintf(stderr, "CACHE files=%zu hits=%lld\n", rows.size(), hits);
    std::fflush(stderr);
    if (hitsOut) *hitsOut = hits;

    const Row *primary = nullptr;
    std::vector<const Row *> waiting;
    PickPrimary(rows, primary, waiting);

    std::string state;
    if (!running) state = "offline";
    else if (!primary) state = "idle";
    else if (primary->v.age > kActiveWindow && primary->v.state != "needs_action") state = "idle";
    else if (primary->v.state == "needs_action" && primary->v.age > kStaleAlertWindow) state = "idle";
    else state = primary->v.state;

    // 余额这一层还没搬过来（HTTP + DPAPI + Key 发现是独立一块），
    // 所以先固定出"已禁用"那一格 —— 与 python snapshot(want_balance=False) 同形。
    // 余额：--no-balance 时与 Python 一样给"已禁用"那一格；要查时按 TTL 与
    // refresh.token 决定这一轮是否真的发请求
    Balance bal;
    if (wantBalance) {
        double token = RefreshTokenMtime();
        bal = FetchBalance(ts, token > 0 && token > gCacheAt);
    } else {
        bal.available = false; bal.error = "已禁用"; bal.skipped = true;
    }
    std::string money = "--";
    if (wantBalance && bal.available) {
        std::string sym = bal.currency == "USD" ? "$" : (bal.currency == "CNY" ? "¥" : "");
        money = sym + (bal.total.empty() ? "0" : bal.total);
    }

    W w;
    w.o += '{';
    w.boolean("ok", true);
    { char buf[32]; snprintf(buf, sizeof buf, "%.6f", ts); w.key("generated_at"); w.o += buf; }
    w.boolean("app_running", running);
    w.str("state", state);

    Text t = ComposeText(running ? primary : nullptr, state, money, waiting.size());
    w.str("label", t.label);
    w.str("glyph", t.glyph);
    w.str("color", t.color);
    w.str("detail", t.detail);
    w.str("strip", t.strip);
    w.str("strip_left", t.stripLeft);
    w.str("strip_right", t.stripRight);
    w.str("tooltip", t.tooltip);
    w.str("tooltip_full", t.tooltipFull);
    w.key("tip_lines"); w.o += '[';
    for (size_t i = 0; i < t.tipLines.size(); ++i) { if (i) w.o += ','; json::EmitStr(w.o, t.tipLines[i]); }
    w.o += ']';
    w.boolean("attention", state == "needs_action" || state == "error");

    if (running && primary) {
        w.begin("session");
        EmitSessionDict(w, *primary);
        w.end();
    } else { w.nul("session"); }

    w.key("waiting"); w.o += '[';
    size_t emitted = 0;
    for (const Row *s : waiting) {
        if (emitted >= 8) break;
        if (primary && s->key == primary->key) continue;
        if (emitted) w.o += ',';
        w.o += '{';
        bool saved = w.first; w.first = true;
        w.str("project", s->project);
        w.optStr("text", s->v.pending.ok ? s->v.pending.text : "");
        { char buf[32]; snprintf(buf, sizeof buf, "%.1f", s->v.age); w.key("age_sec"); w.o += buf; }
        w.str("key", s->key);
        w.o += '}'; w.first = saved;
        ++emitted;
    }
    w.o += ']';

    w.begin("balance");
    EmitBalance(w, bal, wantBalance);
    w.end();

    w.num("sessions_scanned", (long long)rows.size());

    w.key("recent"); w.o += '[';
    size_t rc = 0;
    for (const auto &s : rows) {
        if (s.v.age > kActiveWindow) continue;
        if (rc >= 6) break;
        if (rc) w.o += ',';
        w.o += '{';
        bool saved = w.first; w.first = true;
        w.str("project", s.project);
        w.str("state", s.v.state);
        { char buf[32]; snprintf(buf, sizeof buf, "%.1f", s.v.age); w.key("age_sec"); w.o += buf; }
        w.o += '}'; w.first = saved;
        ++rc;
    }
    w.o += ']';

    w.key("sessions"); w.o += '[';
    size_t n = std::min<size_t>(rows.size(), kSessionsInSnapshot);
    for (size_t i = 0; i < n; ++i) { if (i) w.o += ','; w.o += '{'; bool saved = w.first; w.first = true; EmitSessionRow(w, rows[i]); w.o += '}'; w.first = saved; }
    w.o += ']';

    w.o += '}';
    return w.o;
}

static std::string ToJson(const Verdict &v) {
    W w; w.o += '{'; EmitVerdict(w, v); w.o += '}';
    return w.o;
}

int main(int argc, char **argv) {
#if defined(_WIN32)
    _setmode(_fileno(stdout), _O_BINARY);   // 中文按 UTF-8 原样出，别让 CRT 转码
#endif
    std::string mode = argc > 1 ? argv[1] : "";
    if (mode == "--session" && argc >= 5) {
        std::vector<unsigned char> raw;
        if (!ReadWholeFile(argv[2], raw)) { std::printf("{\"error\":\"读不到文件\"}\n"); return 1; }
        Session s = ReadSessionFromPlain(Unpack(raw));
        std::printf("%s\n", ToJson(Classify(s, strtod(argv[3], nullptr), strtod(argv[4], nullptr))).c_str());
        return 0;
    }
    if (mode == "--classify" && argc >= 4) {
        std::string plain, line;
        while (std::getline(std::cin, line)) { plain += line; plain += '\n'; }
        Session s = ReadSessionFromPlain(plain);
        std::printf("%s\n", ToJson(Classify(s, strtod(argv[2], nullptr), strtod(argv[3], nullptr))).c_str());
        return 0;
    }
    if (mode == "--snapshot" && argc >= 3) {
        bool wantBalance = !(argc >= 4 && std::string(argv[3]) == "--no-balance");
        std::printf("%s\n", Snapshot(strtod(argv[2], nullptr), wantBalance).c_str());
        return 0;
    }
    if (mode == "--watch") {
        // 照 Python 的 --watch：每 interval 秒出一行 NDJSON，供 StateClient 按行读。
        // 余额的 TTL / refresh.token 强刷那一层还没搬，先把节奏与缓存做对。
        double interval = 2.0;
        bool wantBalance = true;
        for (int i = 2; i < argc; ++i) {
            std::string a = argv[i];
            if (a == "--interval" && i + 1 < argc) interval = strtod(argv[++i], nullptr);
            else if (a == "--no-balance") wantBalance = false;
        }
        if (interval <= 0) interval = 2.0;
        for (;;) {
            struct timespec t{};
            timespec_get(&t, TIME_UTC);
            double ts = (double)t.tv_sec + t.tv_nsec / 1e9;
            std::printf("%s\n", Snapshot(ts, wantBalance).c_str());
            std::fflush(stdout);
            // 分片睡：整段 Sleep(interval) 时外部 terminate/kill 也是立刻生效的，
            // 但分片能让"睡够就走"不累积误差，且将来插 refresh.token 检查有落点。
            for (double slept = 0; slept < interval; slept += 0.1) {
#if defined(_WIN32)
                Sleep(100);
#else
                struct timespec nap{0, 100000000L}; nanosleep(&nap, nullptr);
#endif
            }
        }
        return 0;
    }
    if (mode == "--save-balance-key") {
        // Key 只从 stdin 进：argv 不是秘密存放处（本机任何进程都读得到子进程命令行）
        std::string key, line;
        while (std::getline(std::cin, line)) {
            while (!line.empty() && (line.back() == '\r' || line.back() == ' ')) line.pop_back();
            key += line;
        }
        if (key.empty()) { std::fprintf(stderr, "没有读到 Key，已取消。\n"); return 2; }
        std::printf("已保存到 %s\n", SaveBalanceKey(key).c_str());
        return 0;
    }
    if (mode == "--clear-balance-key") {
        std::string removed;
        if (ClearBalanceKey(removed)) { std::printf("已删除 %s\n", removed.c_str()); return 0; }
        std::printf("没有已保存的 Key\n");
        return 0;
    }
    if (mode == "--balance-probe") {
        // 只报来源与长度，**永不**报 Key 本身：面板那套"只显示来源"的口径
        // 不能因为多了个诊断入口就漏出去
        KeyInfo ki = BalanceKey();
        std::printf("source=%s len=%zu\n", ki.source.c_str(), ki.key.size());
        return 0;
    }
    std::printf("用法: vigil-engine --session <会话文件> <mtime> <now>\n"
                "      vigil-engine --classify <mtime> <now>  < records.jsonl\n"
                "      vigil-engine --snapshot <now> [--no-balance]\n"
                "      vigil-engine --watch [--interval 秒] [--no-balance]\n"
                "      vigil-engine --save-balance-key   (Key 从 stdin 进)\n"
                "      vigil-engine --clear-balance-key\n"
                "      vigil-engine --balance-probe\n");
    return 2;
}
