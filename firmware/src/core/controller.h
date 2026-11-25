// PrintLocker controller logic: the protocol and the delivery state machine. Hardware-independent.
// Behaviour spec: kiosk-agent/agent/twin.py; acceptance tests: kiosk-agent/tests/test_controller_protocol.py
#pragma once
#include <map>
#include <string>
#include <vector>

#include "hal.h"
#include "json.h"

#define PL_FIRMWARE_VERSION "0.1.0"

struct Geometry {
  float receiveMm = 346;     // platform top when catching paper under the printer exit
  float firstFloorMm = 516;  // compartment 1 floor
  float pitchMm = 90;
  float pushMm = 320;
  float liftSpeed = 80;      // mm/s
  float pushSpeed = 100;     // mm/s
  float rampS = 0.4f;        // accel + decel time per move (estimate shown in step events)
  uint32_t settleMs = 5000;  // no new sheet for this long = printer finished
  uint32_t pulseMs = 500;    // lock solenoid pulse
  uint32_t doorWaitMs = 60000;
  float floor(int slot) const { return firstFloorMm + (slot - 1) * pitchMm; }
};

class Controller {
 public:
  explicit Controller(Hal& hal, Geometry g = Geometry()) : hal_(hal), g_(g) {}
  void begin();                         // sends the boot event
  void onLine(const std::string& line); // one line from the host
  void tick();                          // call as often as possible

 private:
  enum Phase { IDLE, SETTLE, LIFT_UP, PUSH, PUSH_HOME, LIFT_DOWN, PAUSED, FAILED };
  struct Column {
    int no = 0;
    Phase phase = IDLE;
    Phase resume = IDLE;
    long ref = -1;           // request id of the running delivery (-1 = operator clear)
    int slot = 0;
    int sheets = 0;
    uint32_t started = 0, phaseStart = 0, pausedAt = 0, lastCountChange = 0;
    uint32_t lastCount = 0;
    std::string error;
    bool busy() const { return phase != IDLE && phase != FAILED; }
  };
  struct Lock { int no, column, slot; };

  void handle(const Json& m, long id);
  void reply(long id, bool ok, const std::string& extra = "", const std::string& error = "");
  void event(const std::string& json) { hal_.send(json.c_str()); }
  void startMove(Column& c, Phase p);
  void finishMove(Column& c);
  void fail(Column& c, const std::string& msg);
  int openDoor(int column);
  const Lock* lockAt(int column, int slot) const;
  static const char* name(Phase p);
  static Axis axisOf(Phase p) { return (p == PUSH || p == PUSH_HOME) ? PUSHER : LIFT; }

  Hal& hal_;
  Geometry g_;
  std::map<int, Column> cols_;
  std::map<int, Lock> locks_;
};
