#include "json.h"

#include <cstdio>
#include <cstdlib>

namespace {

struct Parser {
  const std::string& t;
  size_t i = 0;
  explicit Parser(const std::string& text) : t(text) {}

  void ws() {
    while (i < t.size() && (t[i] == ' ' || t[i] == '\t' || t[i] == '\r' || t[i] == '\n')) i++;
  }
  bool lit(const char* w) {
    size_t n = 0;
    while (w[n]) n++;
    if (t.compare(i, n, w) != 0) return false;
    i += n;
    return true;
  }
  bool str(std::string& out) {
    if (i >= t.size() || t[i] != '"') return false;
    i++;
    while (i < t.size() && t[i] != '"') {
      char c = t[i++];
      if (c == '\\' && i < t.size()) {
        char e = t[i++];
        switch (e) {
          case 'n': out += '\n'; break;
          case 't': out += '\t'; break;
          case 'r': out += '\r'; break;
          case 'u':  // keep it simple: skip the 4 hex digits, store '?'
            i += 4;
            out += '?';
            break;
          default: out += e;
        }
      } else {
        out += c;
      }
    }
    if (i >= t.size()) return false;
    i++;
    return true;
  }
  bool value(Json& v, int depth) {
    if (depth > 8) return false;
    ws();
    if (i >= t.size()) return false;
    char c = t[i];
    if (c == '{') {
      v.type = Json::OBJ;
      i++;
      ws();
      if (i < t.size() && t[i] == '}') { i++; return true; }
      while (true) {
        ws();
        std::string k;
        if (!str(k)) return false;
        ws();
        if (i >= t.size() || t[i] != ':') return false;
        i++;
        if (!value(v.o[k], depth + 1)) return false;
        ws();
        if (i < t.size() && t[i] == ',') { i++; continue; }
        if (i < t.size() && t[i] == '}') { i++; return true; }
        return false;
      }
    }
    if (c == '[') {
      v.type = Json::ARR;
      i++;
      ws();
      if (i < t.size() && t[i] == ']') { i++; return true; }
      while (true) {
        v.a.emplace_back();
        if (!value(v.a.back(), depth + 1)) return false;
        ws();
        if (i < t.size() && t[i] == ',') { i++; continue; }
        if (i < t.size() && t[i] == ']') { i++; return true; }
        return false;
      }
    }
    if (c == '"') { v.type = Json::STR; return str(v.s); }
    if (lit("true")) { v.type = Json::BOOL; v.b = true; return true; }
    if (lit("false")) { v.type = Json::BOOL; v.b = false; return true; }
    if (lit("null")) { v.type = Json::NUL; return true; }
    const char* start = t.c_str() + i;
    char* end = nullptr;
    double d = std::strtod(start, &end);
    if (end == start) return false;
    i += static_cast<size_t>(end - start);
    v.type = Json::NUM;
    v.n = d;
    return true;
  }
};

const Json kNull;

}  // namespace

const Json& Json::operator[](const std::string& k) const {
  if (type != OBJ) return kNull;
  auto it = o.find(k);
  return it == o.end() ? kNull : it->second;
}

bool parseJson(const std::string& text, Json& out) {
  Parser p(text);
  out = Json();
  if (!p.value(out, 0)) return false;
  p.ws();
  return p.i == text.size();
}

std::string jsonEscape(const std::string& s) {
  std::string o = "\"";
  for (char c : s) {
    if (c == '"' || c == '\\') { o += '\\'; o += c; }
    else if (c == '\n') o += "\\n";
    else if (static_cast<unsigned char>(c) < 0x20) o += ' ';
    else o += c;
  }
  return o + "\"";
}

void JsonOut::key(const char* k) {
  if (!body_.empty()) body_ += ',';
  body_ += jsonEscape(k);
  body_ += ':';
}

JsonOut& JsonOut::add(const char* k, long v) {
  key(k);
  body_ += std::to_string(v);
  return *this;
}

JsonOut& JsonOut::add(const char* k, double v) {
  key(k);
  char buf[32];
  std::snprintf(buf, sizeof buf, "%.2f", v);
  body_ += buf;
  return *this;
}

JsonOut& JsonOut::add(const char* k, bool v) {
  key(k);
  body_ += v ? "true" : "false";
  return *this;
}

JsonOut& JsonOut::add(const char* k, const char* v) {
  key(k);
  body_ += jsonEscape(v);
  return *this;
}

JsonOut& JsonOut::raw(const char* k, const std::string& json) {
  key(k);
  body_ += json;
  return *this;
}

JsonOut& JsonOut::null(const char* k) {
  key(k);
  body_ += "null";
  return *this;
}
