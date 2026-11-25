// Minimal JSON for the controller protocol: one flat object per line, plus arrays of flat objects
// ("config" -> "locks"). No external library, so the same code builds for the ESP32 and the PC.
#pragma once
#include <map>
#include <string>
#include <vector>

struct Json {
  enum Type { NUL, BOOL, NUM, STR, ARR, OBJ } type = NUL;
  bool b = false;
  double n = 0;
  std::string s;
  std::vector<Json> a;
  std::map<std::string, Json> o;

  bool has(const std::string& k) const { return type == OBJ && o.count(k); }
  const Json& operator[](const std::string& k) const;
  bool isNum() const { return type == NUM; }
  long asInt() const { return static_cast<long>(n); }
};

// Parses one JSON value. Returns false on malformed input.
bool parseJson(const std::string& text, Json& out);

// Writer: JsonOut().add("id", 3).add("ok", true).str() -> {"id":3,"ok":true}
class JsonOut {
 public:
  JsonOut& add(const char* k, long v);
  JsonOut& add(const char* k, int v) { return add(k, static_cast<long>(v)); }
  JsonOut& add(const char* k, double v);
  JsonOut& add(const char* k, bool v);
  JsonOut& add(const char* k, const char* v);
  JsonOut& add(const char* k, const std::string& v) { return add(k, v.c_str()); }
  JsonOut& raw(const char* k, const std::string& json);  // nested value already encoded
  JsonOut& null(const char* k);
  std::string str() const { return "{" + body_ + "}"; }

 private:
  void key(const char* k);
  std::string body_;
};

std::string jsonEscape(const std::string& s);
