// The controller firmware running on a PC against a simulated board.
// Same core code as the ESP32 build; talks JSON lines on stdin/stdout.
//
//   ./build/controller_native [--speed 1]      1 = real time, 0 = as fast as possible
//
// Faults for testing: PL_SIM_FAULT=lift_stall|pusher_stall|stack_missing (first delivery only),
// PL_SIM_SHEETS=n makes the platform counter report n sheets on every delivery.
#include <poll.h>
#include <unistd.h>

#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <map>
#include <string>

#include "../core/controller.h"

namespace {

struct AxisSim {
  float pos = 0, from = 0, to = 0;
  uint32_t t0 = 0, dur = 0;
  bool moving = false, stall = false;
};

class SimHal : public Hal {
 public:
  explicit SimHal(const Geometry& g) : g_(g) {
    const char* f = std::getenv("PL_SIM_FAULT");
    fault_ = f ? f : "";
    const char* s = std::getenv("PL_SIM_SHEETS");
    forcedSheets_ = s ? std::atoi(s) : -1;
  }
  uint32_t now = 0;  // virtual milliseconds, advanced by main()

  uint32_t ms() override { return now; }
  void send(const char* line) override {
    std::fputs(line, stdout);
    std::fputc('\n', stdout);
    std::fflush(stdout);
  }
  bool configureLock(int lock, int column, int slot) override {
    locks_[lock] = {column, slot};
    return true;
  }
  void home(int column) override {
    ax(column, LIFT) = AxisSim();
    ax(column, LIFT).pos = g_.receiveMm;
    ax(column, PUSHER) = AxisSim();
  }
  void moveTo(int column, Axis a, float mm, float speed) override {
    AxisSim& s = ax(column, a);
    update(column, a);
    s.from = s.pos;
    s.to = mm;
    s.t0 = now;
    s.dur = static_cast<uint32_t>((std::fabs(mm - s.pos) / speed + g_.rampS) * 1000);
    s.moving = true;
    s.stall = false;
    if ((fault_ == "lift_stall" && a == LIFT && mm > s.pos) || (fault_ == "pusher_stall" && a == PUSHER && mm > 0)) {
      s.to = s.pos + (mm - s.pos) / 2;  // stalls half way
      s.dur /= 2;
      s.stall = true;
      fault_.clear();
    }
  }
  void stop(int column, Axis a) override {
    update(column, a);
    ax(column, a).moving = false;
  }
  bool moving(int column, Axis a) override {
    update(column, a);
    return ax(column, a).moving;
  }
  float pos(int column, Axis a) override {
    update(column, a);
    return ax(column, a).pos;
  }
  bool stalled(int column, Axis a) override {
    update(column, a);
    AxisSim& s = ax(column, a);
    return s.stall && !s.moving;
  }
  void hold(int, bool) override {}
  uint32_t platformSheets(int column) override {
    return forcedSheets_ >= 0 ? static_cast<uint32_t>(forcedSheets_) : platform_[column];
  }
  void resetPlatform(int column) override { platform_[column] = 0; }
  bool doorClosed(int lock) override { return !open_[lock]; }
  bool paperIn(int lock) override { return paper_[lock]; }
  void unlock(int lock, uint32_t) override { paper_[lock] = false; }  // nobody is there; user takes paper

 private:
  AxisSim& ax(int column, Axis a) { return axes_[column * 2 + a]; }
  void update(int column, Axis a) {
    AxisSim& s = ax(column, a);
    if (!s.moving) return;
    float f = s.dur ? std::min(1.0f, static_cast<float>(now - s.t0) / s.dur) : 1.0f;
    s.pos = s.from + (s.to - s.from) * f;
    if (f >= 1.0f) {
      s.moving = false;
      if (s.stall) return;  // stopped where it stalled
      if (a == PUSHER && s.to >= g_.pushMm - 1) transfer(column);
    }
  }
  void transfer(int column) {  // the stack slides off the platform into the compartment at lift height
    float lift = ax(column, LIFT).pos;
    for (auto& kv : locks_) {
      if (kv.second.first != column) continue;
      if (std::fabs(g_.floor(kv.second.second) - lift) < 2) {
        if (fault_ == "stack_missing") { fault_.clear(); return; }
        paper_[kv.first] = true;
      }
    }
  }
  Geometry g_;
  std::string fault_;
  int forcedSheets_;
  std::map<int, AxisSim> axes_;
  std::map<int, std::pair<int, int>> locks_;
  std::map<int, uint32_t> platform_;
  std::map<int, bool> open_, paper_;
};

}  // namespace

int main(int argc, char** argv) {
  double speed = 0;
  for (int i = 1; i < argc; i++)
    if (!std::strcmp(argv[i], "--speed") && i + 1 < argc) speed = std::atof(argv[++i]);

  Geometry g;
  SimHal hal(g);
  Controller ctl(hal, g);
  ctl.begin();

  std::string buf;
  const uint32_t step = 10;  // ms of machine time per loop
  while (true) {
    struct pollfd p = {STDIN_FILENO, POLLIN, 0};
    int r = poll(&p, 1, 0);
    if (r > 0) {
      char chunk[1024];
      ssize_t n = read(STDIN_FILENO, chunk, sizeof chunk);
      if (n <= 0) return 0;  // host closed the line
      buf.append(chunk, static_cast<size_t>(n));
      size_t nl;
      while ((nl = buf.find('\n')) != std::string::npos) {
        ctl.onLine(buf.substr(0, nl));
        buf.erase(0, nl + 1);
      }
    }
    ctl.tick();
    hal.now += step;
    if (speed > 0) usleep(static_cast<useconds_t>(step * 1000 * speed));
    else if (r == 0) usleep(20);  // as fast as possible, without burning a whole core
  }
}
