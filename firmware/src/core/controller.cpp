#include "controller.h"

#include <cmath>
#include <cstdio>

namespace {
std::string fmt(const char* f, double a = 0, double b = 0) {
  char buf[160];
  std::snprintf(buf, sizeof buf, f, a, b);
  return buf;
}
}  // namespace

const char* Controller::name(Phase p) {
  switch (p) {
    case IDLE: return "idle";
    case SETTLE: return "settle";
    case LIFT_UP: return "lift_up";
    case PUSH: return "push";
    case PUSH_HOME: return "pusher_home";
    case LIFT_DOWN: return "lift_down";
    case PAUSED: return "paused";
    case FAILED: return "error";
  }
  return "?";
}

void Controller::begin() {
  event(JsonOut().add("event", "boot").add("version", PL_FIRMWARE_VERSION).str());
}

// ------------------------------------------------------------------ protocol
void Controller::reply(long id, bool ok, const std::string& extra, const std::string& error) {
  std::string body = JsonOut().add("id", id).add("ok", ok).str();
  body.pop_back();  // drop '}' to append more fields
  if (!ok) body += "," + std::string("\"error\":") + jsonEscape(error);
  if (!extra.empty()) body += "," + extra;
  hal_.send((body + "}").c_str());
}

void Controller::onLine(const std::string& line) {
  Json m;
  if (!parseJson(line, m) || m.type != Json::OBJ) return;  // free text / garbage: ignore
  long id = m["id"].isNum() ? m["id"].asInt() : -1;
  handle(m, id);
}

void Controller::handle(const Json& m, long id) {
  const std::string cmd = m["cmd"].type == Json::STR ? m["cmd"].s : "";

  if (cmd == "config") {
    const Json& locks = m["locks"];
    if (locks.type != Json::ARR) return reply(id, false, "", "bad command: locks missing");
    std::map<int, Lock> next;
    for (const Json& l : locks.a) {
      if (!l["lock"].isNum() || !l["column"].isNum() || !l["slot"].isNum())
        return reply(id, false, "", "bad command: lock entries need lock, column, slot");
      Lock lk{static_cast<int>(l["lock"].asInt()), static_cast<int>(l["column"].asInt()),
              static_cast<int>(l["slot"].asInt())};
      if (!hal_.configureLock(lk.no, lk.column, lk.slot))
        return reply(id, false, "", fmt("this board cannot drive lock %.0f (column %.0f)", lk.no, lk.column));
      next[lk.no] = lk;
    }
    for (auto& kv : cols_)
      if (kv.second.busy()) return reply(id, false, "", "cannot reconfigure while a column is moving");
    locks_ = next;
    std::map<int, Column> cols;
    for (auto& kv : locks_) {
      int c = kv.second.column;
      if (!cols.count(c)) {
        cols[c].no = c;
        hal_.home(c);
      }
    }
    cols_ = cols;
    return reply(id, true, "\"locks\":" + std::to_string(locks_.size()));
  }

  if (cmd == "deliver") {
    if (!m["column"].isNum() || !m["slot"].isNum() || !m["sheets"].isNum())
      return reply(id, false, "", "bad command: deliver needs column, slot, sheets");
    int col = static_cast<int>(m["column"].asInt()), slot = static_cast<int>(m["slot"].asInt());
    auto it = cols_.find(col);
    if (it == cols_.end()) return reply(id, false, "", fmt("no column %.0f", col));
    Column& c = it->second;
    if (c.busy()) return reply(id, false, "", fmt("column %.0f is busy", col));
    if (c.phase == FAILED) return reply(id, false, "", fmt("column %.0f needs an operator: ", col) + c.error);
    if (!lockAt(col, slot)) return reply(id, false, "", fmt("no compartment %.0f in column %.0f", slot, col));
    c.ref = id;
    c.slot = slot;
    c.sheets = static_cast<int>(m["sheets"].asInt());
    c.started = c.phaseStart = hal_.ms();
    c.phase = SETTLE;
    c.error.clear();
    event(JsonOut().add("event", "step").add("column", col).add("step", "settle")
              .add("seconds", g_.settleMs / 1000.0).str());
    return reply(id, true, "\"accepted\":true");
  }

  if (cmd == "unlock") {
    if (!m["lock"].isNum()) return reply(id, false, "", "bad command: unlock needs lock");
    int lock = static_cast<int>(m["lock"].asInt());
    if (!locks_.count(lock)) return reply(id, false, "", fmt("no lock %.0f", lock));
    hal_.unlock(lock, g_.pulseMs);
    event(JsonOut().add("event", "lock_pulse").add("lock", lock).add("seconds", g_.pulseMs / 1000.0).str());
    return reply(id, true);
  }

  if (cmd == "sensors") {
    std::string arr = "[";
    for (auto& kv : locks_) {
      if (arr.size() > 1) arr += ",";
      arr += JsonOut().add("lock", kv.first).add("door_closed", hal_.doorClosed(kv.first))
                 .add("paper", hal_.paperIn(kv.first)).str();
    }
    return reply(id, true, "\"sensors\":" + arr + "]");
  }

  if (cmd == "status") {
    std::string arr = "[";
    for (auto& kv : cols_) {
      Column& c = kv.second;
      if (arr.size() > 1) arr += ",";
      JsonOut o;
      o.add("no", c.no).add("phase", name(c.phase)).add("busy", c.busy())
          .add("lift_mm", static_cast<double>(hal_.pos(c.no, LIFT)))
          .add("pusher_mm", static_cast<double>(hal_.pos(c.no, PUSHER)))
          .add("platform_sheets", static_cast<long>(hal_.platformSheets(c.no)));
      if (c.error.empty()) o.null("error"); else o.add("error", c.error);
      arr += o.str();
    }
    return reply(id, true, "\"version\":\"" PL_FIRMWARE_VERSION "\",\"columns\":" + arr + "]");
  }

  if (cmd == "clear") {
    if (!m["column"].isNum()) return reply(id, false, "", "bad command: clear needs column");
    auto it = cols_.find(static_cast<int>(m["column"].asInt()));
    if (it == cols_.end()) return reply(id, false, "", "no such column");
    Column& c = it->second;
    if (c.busy()) return reply(id, false, "", "column is moving");
    c.error.clear();
    hal_.resetPlatform(c.no);
    c.ref = -1;  // operator clear: park the mechanism, no delivered event
    startMove(c, PUSH_HOME);
    return reply(id, true);
  }

  if (cmd.empty()) return reply(id, false, "", "bad command: cmd missing");
  reply(id, false, "", "unknown command '" + cmd + "'");
}

// ------------------------------------------------------------------ delivery state machine
const Controller::Lock* Controller::lockAt(int column, int slot) const {
  for (auto& kv : locks_)
    if (kv.second.column == column && kv.second.slot == slot) return &kv.second;
  return nullptr;
}

int Controller::openDoor(int column) {
  for (auto& kv : locks_)
    if (kv.second.column == column && !hal_.doorClosed(kv.first)) return kv.first;
  return 0;
}

void Controller::startMove(Column& c, Phase p) {
  Axis ax = axisOf(p);
  float target = p == LIFT_UP ? g_.floor(c.slot) : p == PUSH ? g_.pushMm : p == PUSH_HOME ? 0 : g_.receiveMm;
  float speed = ax == LIFT ? g_.liftSpeed : g_.pushSpeed;
  float from = hal_.pos(c.no, ax);
  c.phase = p;
  c.phaseStart = hal_.ms();
  hal_.hold(c.no, true);
  if (std::fabs(target - from) < 0.5f) return;  // already there; tick() moves on
  hal_.moveTo(c.no, ax, target, speed);
  event(JsonOut().add("event", "step").add("column", c.no).add("step", name(p))
            .add("from_mm", static_cast<long>(std::lround(from))).add("to_mm", static_cast<long>(std::lround(target)))
            .add("seconds", std::fabs(target - from) / speed + g_.rampS).str());
}

void Controller::fail(Column& c, const std::string& msg) {
  hal_.stop(c.no, LIFT);
  hal_.stop(c.no, PUSHER);
  c.phase = FAILED;
  c.error = msg;  // drivers stay enabled: a raised lift must not slide down
  if (c.ref >= 0)
    event(JsonOut().add("event", "delivery_failed").add("ref", c.ref).add("column", c.no).add("error", msg).str());
}

void Controller::finishMove(Column& c) {
  switch (c.phase) {
    case LIFT_UP: startMove(c, PUSH); break;
    case PUSH: {
      const Lock* lk = lockAt(c.no, c.slot);
      if (!lk || !hal_.paperIn(lk->no)) return fail(c, "compartment sensor sees no paper after the push");
      hal_.resetPlatform(c.no);
      startMove(c, PUSH_HOME);
      break;
    }
    case PUSH_HOME: startMove(c, LIFT_DOWN); break;
    case LIFT_DOWN: {
      c.phase = IDLE;
      hal_.hold(c.no, false);  // resting on the bottom stop
      if (c.ref >= 0)
        event(JsonOut().add("event", "delivered").add("ref", c.ref).add("column", c.no).add("slot", c.slot)
                  .add("sheets", c.sheets).add("seconds", (hal_.ms() - c.started) / 1000.0).str());
      c.ref = -1;
      break;
    }
    default: break;
  }
}

void Controller::tick() {
  hal_.poll();
  uint32_t now = hal_.ms();
  for (auto& kv : cols_) {
    Column& c = kv.second;
    uint32_t count = hal_.platformSheets(c.no);
    if (count != c.lastCount) {
      c.lastCount = count;
      c.lastCountChange = now;
    }
    switch (c.phase) {
      case IDLE:
      case FAILED:
        break;
      case SETTLE: {
        uint32_t since = c.lastCountChange > c.phaseStart ? c.lastCountChange : c.phaseStart;
        if (now - since < g_.settleMs) break;
        // A counter reading of 0 means no sheet counter is fitted: trust the job.
        if (count != 0 && static_cast<int>(count) != c.sheets) {
          fail(c, fmt("platform counted %.0f sheets, expected %.0f", count, c.sheets));
          break;
        }
        if (int d = openDoor(c.no)) {
          c.resume = LIFT_UP;
          c.phase = PAUSED;
          c.pausedAt = now;
          event(JsonOut().add("event", "paused").add("column", c.no).add("reason", fmt("door %.0f open", d)).str());
          break;
        }
        startMove(c, LIFT_UP);
        break;
      }
      case LIFT_UP:
      case PUSH:
      case PUSH_HOME:
      case LIFT_DOWN: {
        Axis ax = axisOf(c.phase);
        if (int d = openDoor(c.no)) {  // interlock: nothing moves while a door of this column is open
          hal_.stop(c.no, LIFT);
          hal_.stop(c.no, PUSHER);
          c.resume = c.phase;
          c.phase = PAUSED;
          c.pausedAt = now;
          event(JsonOut().add("event", "paused").add("column", c.no).add("reason", fmt("door %.0f open", d)).str());
          break;
        }
        if (hal_.stalled(c.no, ax)) {
          fail(c, fmt(ax == LIFT ? "lift stalled (StallGuard) at %.0f mm" : "pusher stalled (StallGuard) at %.0f mm",
                      hal_.pos(c.no, ax)));
          break;
        }
        if (!hal_.moving(c.no, ax)) finishMove(c);
        break;
      }
      case PAUSED: {
        int d = openDoor(c.no);
        if (!d) {
          event(JsonOut().add("event", "resumed").add("column", c.no).str());
          startMove(c, c.resume);  // re-issues the move from where it stopped
        } else if (now - c.pausedAt > g_.doorWaitMs) {
          fail(c, fmt("door %.0f stayed open; delivery stopped", d));
        }
        break;
      }
    }
  }
}
