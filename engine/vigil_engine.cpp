// Vigil 状态引擎核心：JSONL 会话记录 → 状态判定。
//
// 边界是刻意切的：本文件**不碰文件、不碰 zstd**，从 stdin 读已解压的 JSONL，
// 吐一行判定结果 JSON。为什么切在这里 —— 会话文件是 session.v4.jsonl.zstd，
// 而本机实测 Windows 自带压缩 API 解不了 zstd（见 probe_zstd 的结论），
// 所以"解压"这一层是谁做的、用什么做，还是个待决问题；判定这一层则完全
// 与解压方式无关，可以先写、先测、先跟 Python 逐字节对齐。
//
// 判定逻辑照 dsh_state.py 的 classify() 一比一搬，阈值同名同值：
// ACTIVE_WINDOW / DONE_WINDOW / STALL_WINDOW 等常量集中在这里，两处改一边
// 冒烟会红（smoke_test 有"前端映射逐项等于引擎 STATES"的门禁）。
//
// 用法：  vigil-engine --classify <mtime> <now>   < records.jsonl
// 依赖：  只有 Win32 + 标准库。没有第三方。
#include <cstdio>
#include <cstdint>
#include <cctype>
#include <cstring>
#include <algorithm>
#include <fstream>
#include <iostream>
#include <map>
#include <memory>
#include <sstream>
#include <string>
#include <vector>
#if defined(_WIN32)
#include <fcntl.h>
#include <io.h>
#endif

// ---------------------------------------------------------------- 状态表
// 与 dsh_state.py 的 STATES、bar/Panel/Ui.cs 的 StateMap 三方逐项一致。
struct StateInfo { const char *code; const char *label; const char *color; int priority; };
static const StateInfo kStates[] = {
    {"needs_action", "需要操作", "#D87D44", 90},
    {"error",        "出错了",   "#C65B51", 80},
    {"stalled",      "疑似卡住", "#C3A559", 70},
    {"thinking",     "正在思考", "#92A5A0", 60},
    {"tool_running", "正在执行工具", "#54644A", 58},
    {"answering",    "正在回答", "#7F8E6B", 56},
    {"done",         "回答完成", "#BFD268", 40},
    {"aborted",      "已中断",   "#5A6363", 30},
    {"idle",         "待命",     "#D2D2C8", 20},
    {"offline",      "未运行",   "#2E3844", 10},
    {"unknown",      "未知",     "#8E918A", 0},
};
static int PriorityOf(const std::string &code) {
    for (const auto &s : kStates) if (code == s.code) return s.priority;
    return 0;
}

static const double kDoneWindow = 25.0;
static const double kStallWindow = 120.0;
static const size_t kTailEvents = 500;

// 会阻塞等用户回答的工具 → 中文说法（照 Python 的 BLOCKING_TOOLS）
static const std::map<std::string, std::string> kBlockingTools = {
    {"ask_user_question", "问题"},
    {"exit_plan_mode", "计划确认"},
};

// ---------------------------------------------------------------- 极简 JSON
// 只需要读会话记录，所以不做数字精度、不做 \u 代理对的完整实现：
// 会话里的数字只有 turn/step/token 计数，字符串都是 UTF-8 原文。
struct JValue {
    enum Kind { Null, Bool, Num, Str, Arr, Obj } kind = Null;
    bool b = false;
    double num = 0;
    std::string str;
    std::vector<JValue> arr;
    std::vector<std::pair<std::string, JValue>> obj;   // 保序，量不大

    const JValue *find(const std::string &key) const {
        if (kind != Obj) return nullptr;
        for (const auto &kv : obj) if (kv.first == key) return &kv.second;
        return nullptr;
    }
    const JValue *at(const std::string &key) const {
        const JValue *v = find(key);
        return (v && v->kind != Null) ? v : nullptr;
    }
    std::string asStr(const std::string &def = "") const {
        return kind == Str ? str : def;
    }
};

namespace json {

struct Parser {
    const std::string &s;
    size_t i = 0;
    explicit Parser(const std::string &src) : s(src) {}

    void Skip() { while (i < s.size() && (s[i] == ' ' || s[i] == '\t' || s[i] == '\r' || s[i] == '\n')) ++i; }
    bool Fail = false;

    void ParseValue(JValue &v) {
        Skip();
        if (i >= s.size()) { Fail = true; return; }
        char c = s[i];
        if (c == '{') return ParseObj(v);
        if (c == '[') return ParseArr(v);
        if (c == '"') { v.kind = JValue::Str; ParseStr(v.str); return; }
        if (c == 't' || c == 'f') { v.kind = JValue::Bool; v.b = (c == 't'); i += 4 + (c == 't' ? 0 : 1); return; }
        if (c == 'n') { v.kind = JValue::Null; i += 4; return; }
        v.kind = JValue::Num;
        size_t start = i;
        while (i < s.size() && (isdigit((unsigned char)s[i]) || s[i] == '-' || s[i] == '+' ||
                                s[i] == '.' || s[i] == 'e' || s[i] == 'E')) ++i;
        if (i == start) { Fail = true; return; }
        v.num = strtod(s.substr(start, i - start).c_str(), nullptr);
    }
    void ParseStr(std::string &out) {
        if (s[i] != '"') { Fail = true; return; }
        ++i;
        std::string r;
        while (i < s.size()) {
            char c = s[i++];
            if (c == '"') { out = r; return; }
            if (c == '\\') {
                if (i >= s.size()) break;
                char e = s[i++];
                switch (e) {
                    case 'n': r += '\n'; break;
                    case 't': r += '\t'; break;
                    case 'r': r += '\r'; break;
                    case 'b': r += '\b'; break;
                    case 'f': r += '\f'; break;
                    case '/': r += '/'; break;
                    case '"': r += '"'; break;
                    case '\\': r += '\\'; break;
                    case 'u': {
                        if (i + 4 > s.size()) { Fail = true; return; }
                        unsigned cp = strtoul(s.substr(i, 4).c_str(), nullptr, 16);
                        i += 4;
                        // 只处理 BMP；会话里出现的 \u 极少，代理对先按 ？ 占位
                        if (cp < 0x80) r += (char)cp;
                        else if (cp < 0x800) { r += (char)(0xC0 | (cp >> 6)); r += (char)(0x80 | (cp & 0x3F)); }
                        else { r += (char)(0xE0 | (cp >> 12)); r += (char)(0x80 | ((cp >> 6) & 0x3F));
                               r += (char)(0x80 | (cp & 0x3F)); }
                        break;
                    }
                    default: r += e;
                }
            } else r += c;
        }
        Fail = true;
    }
    void ParseArr(JValue &v) {
        v.kind = JValue::Arr; ++i;  // [
        Skip();
        if (i < s.size() && s[i] == ']') { ++i; return; }
        while (i < s.size()) {
            JValue e; ParseValue(e);
            if (Fail) return;
            v.arr.push_back(std::move(e));
            Skip();
            if (i < s.size() && s[i] == ',') { ++i; continue; }
            if (i < s.size() && s[i] == ']') { ++i; return; }
            Fail = true; return;
        }
        Fail = true;
    }
    void ParseObj(JValue &v) {
        v.kind = JValue::Obj; ++i;  // {
        Skip();
        if (i < s.size() && s[i] == '}') { ++i; return; }
        while (i < s.size()) {
            Skip();
            std::string k; ParseStr(k);
            if (Fail) return;
            Skip();
            if (i >= s.size() || s[i] != ':') { Fail = true; return; }
            ++i;
            JValue val; ParseValue(val);
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
    Parser p(src); p.ParseValue(out);
    return !p.Fail;
}

// 输出转义：状态文本要进 JSON，中文原样出（与 Python 的 ensure_ascii=False 同口径）
static void EmitStr(std::string &o, const std::string &s) {
    o += '"';
    for (unsigned char c : s) {
        if (c == '"') o += "\\\"";
        else if (c == '\\') o += "\\\\";
        else if (c == '\n') o += "\\n";
        else if (c == '\r') o += "\\r";
        else if (c == '\t') o += "\\t";
        else if (c < 0x20) { char buf[8]; snprintf(buf, sizeof buf, "\\u%04X", c); o += buf; }
        else o += (char)c;
    }
    o += '"';
}
}  // namespace json

// ---------------------------------------------------------------- 记录模型
using Events = std::vector<JValue>;

static const JValue *Data(const JValue &e) { return e.at("data"); }

static std::string TypeOf(const JValue &e) {
    const JValue *t = e.at("type");
    return t ? t->asStr() : std::string();
}

static std::vector<const JValue *> ContentBlocks(const JValue &e) {
    std::vector<const JValue *> out;
    const JValue *d = e.at("data");
    if (!d) return out;
    const JValue *m = d->at("message");
    if (!m) return out;
    const JValue *c = m->at("content");
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
        if (!d) continue;
        const JValue *id = d->at("id");
        if (!id) continue;
        std::string t = TypeOf(e);
        if (t == "approval/asked") asked.emplace_back(id->asStr(), d);
        else if (t == "approval/decided") {
            for (size_t i = 0; i < asked.size(); ++i)
                if (asked[i].first == id->asStr()) { asked.erase(asked.begin() + i); break; }
        }
    }
    if (asked.empty()) return {};
    const JValue *p = asked.front().second;
    Pending r; r.ok = true; r.kind = "approval"; r.tool = p->asStr();
    const JValue *tn = p->at("toolName");
    if (tn) r.tool = tn->asStr();
    const JValue *reason = p->at("reason");
    if (reason && reason->kind == JValue::Str && !reason->str.empty()) r.text = reason->str;
    else { r.text = r.tool + " 需要授权"; }
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
        if (!callId.empty() &&
            std::find(resolved.begin(), resolved.end(), callId) != resolved.end()) continue;

        JValue args;
        const JValue *raw = d->at("arguments");
        if (raw && raw->kind == JValue::Str) json::Parse(raw->str, args);

        Pending r; r.ok = true; r.kind = "question"; r.tool = name;
        const JValue *qs = args.kind == JValue::Obj ? args.at("questions") : nullptr;
        if (name == "ask_user_question" && qs && qs->kind == JValue::Arr && !qs->arr.empty()) {
            const JValue &first = qs->arr.front();
            std::string q = first.at("question") ? first.at("question")->asStr() : "";
            if (qs->arr.size() > 1) q += "（共 " + std::to_string(qs->arr.size()) + " 问）";
            r.text = q;
            const JValue *opts = first.at("options");
            if (opts && opts->kind == JValue::Arr)
                for (const auto &o : opts->arr)
                    if (o.kind == JValue::Obj && o.at("label")) r.options.push_back(o.at("label")->asStr());
        } else if (args.kind == JValue::Obj && args.at("question")) {
            r.text = args.at("question")->asStr();
        }
        if (r.text.empty()) r.text = "模型在等待你" + blk->second;
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

// ---------------------------------------------------------------- 判定
struct Verdict {
    std::string state = "idle";
    double age = 0;
    bool hasTurn = false, hasStep = false;
    long turn = 0, step = 0;
    std::string lastEvent, lastTool, endReason;
    Pending pending;
    std::string error;
};

static Verdict Classify(const Events &ev, double mtime, double ts,
                        const JValue *lastTurnEnd) {
    Verdict v;
    v.age = ts - mtime; if (v.age < 0) v.age = 0;
    v.endReason = ReasonKind(lastTurnEnd);
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
        if (lastTurnEnd) {
            const JValue *d = lastTurnEnd->at("data");
            const JValue *r = d ? d->at("reason") : nullptr;
            const JValue *err = r ? r->at("error") : nullptr;
            const JValue *msg = err ? err->at("message") : nullptr;
            if (msg) v.error = msg->asStr().substr(0, 180);
        }
    } else if (v.endReason == "aborted") v.state = "aborted";
    else if (v.endReason == "completed") v.state = (v.age <= kDoneWindow) ? "done" : "idle";
    else v.state = "idle";
    return v;
}

// ---------------------------------------------------------------- 输出
static std::string ToJson(const Verdict &v) {
    std::string o = "{";
    auto key = [&](const char *k) { if (o.size() > 1) o += ','; o += '"'; o += k; o += "\":"; };
    key("state"); json::EmitStr(o, v.state);
    { char buf[64]; key("age_sec"); snprintf(buf, sizeof buf, "%.1f", v.age); o += buf; }
    key("last_event"); if (v.lastEvent.empty()) o += "null"; else json::EmitStr(o, v.lastEvent);
    key("last_tool"); if (v.lastTool.empty()) o += "null"; else json::EmitStr(o, v.lastTool);
    key("end_reason");
    // 恒为字符串：Python 的 _reason_kind() 走 `str(r or "")`，没有 turn/end 时给的是
    // 空串而不是 null。对照器（engine/compare_classify.py）拿 59 个真实会话量出来的
    // 唯一一处不一致就是这个字段 —— 契约上 `""` 才是对的。
    json::EmitStr(o, v.endReason);
    key("turn"); if (v.hasTurn) o += std::to_string(v.turn); else o += "null";
    key("step"); if (v.hasStep) o += std::to_string(v.step); else o += "null";
    key("label");
    for (const auto &s : kStates) if (v.state == s.code) { json::EmitStr(o, s.label); break; }
    key("color");
    for (const auto &s : kStates) if (v.state == s.code) { json::EmitStr(o, s.color); break; }
    { int pr = PriorityOf(v.state); key("priority"); o += std::to_string(pr); }
    key("error"); if (v.error.empty()) o += "null"; else json::EmitStr(o, v.error);
    key("pending");
    if (!v.pending.ok) o += "null";
    else {
        o += "{\"kind\":"; json::EmitStr(o, v.pending.kind);
        o += ",\"tool\":"; if (v.pending.tool.empty()) o += "null"; else json::EmitStr(o, v.pending.tool);
        o += ",\"text\":"; json::EmitStr(o, v.pending.text);
        o += ",\"options\":[";
        for (size_t i = 0; i < v.pending.options.size(); ++i) { if (i) o += ','; json::EmitStr(o, v.pending.options[i]); }
        o += "]}";
    }
    o += "}";
    return o;
}

int main(int argc, char **argv) {
    if (argc < 4 || std::string(argv[1]) != "--classify") {
        std::printf("用法: vigil-engine --classify <mtime> <now>  < records.jsonl\n");
        return 2;
    }
    double mtime = strtod(argv[2], nullptr), ts = strtod(argv[3], nullptr);

    Events ev;
    ev.reserve(1024);
    const JValue *lastTurnEnd = nullptr;
    std::string line;
    while (std::getline(std::cin, line)) {
        // 与 Python 的 _iter_records 同口径：坏行跳过，不整体失败
        if (line.empty() || line[strspn(line.data(), " \t\r")] == 0) continue;
        JValue v;
        if (!json::Parse(line, v) || v.kind != JValue::Obj) continue;
        if (ev.size() >= kTailEvents) ev.erase(ev.begin());   // 只留尾部 N 条
        ev.push_back(std::move(v));
    }
    // last_turn_end 要的是**整文件**最后一条 turn/end，指针可能因 erase 失效，
    // 所以这里在保留下来的尾部里再找一次（Python 侧是全量扫描时记的）
    for (auto it = ev.rbegin(); it != ev.rend(); ++it)
        if (TypeOf(*it) == "turn/end") { lastTurnEnd = &*it; break; }

    Verdict v = Classify(ev, mtime, ts, lastTurnEnd);
    std::string out = ToJson(v);
#if defined(_WIN32)
    _setmode(_fileno(stdout), _O_BINARY);   // 中文按 UTF-8 原样出，别让 CRT 转码
#endif
    std::printf("%s\n", out.c_str());
    return 0;
}
