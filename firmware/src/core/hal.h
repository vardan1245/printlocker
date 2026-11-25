// Hardware abstraction: everything the controller logic needs from the board.
// Implemented by the ESP32 board (src/esp32) and by a simulated board for the PC (src/native).
#pragma once
#include <cstdint>

enum Axis { LIFT = 0, PUSHER = 1 };

class Hal {
 public:
  virtual ~Hal() {}
  virtual uint32_t ms() = 0;                    // monotonic milliseconds
  virtual void send(const char* line) = 0;      // one JSON line to the host (newline added by the HAL)

  // Wiring the host told us about: lock number -> column, slot.
  virtual bool configureLock(int lock, int column, int slot) = 0;  // false = this board can't drive it

  // Motion, in millimetres. moveTo starts a move with the board's ramps and returns at once.
  virtual void home(int column) = 0;            // blocking: lift to bottom stop, pusher to home switch
  virtual void moveTo(int column, Axis ax, float mm, float mmPerS) = 0;
  virtual void stop(int column, Axis ax) = 0;   // ramp down and stop; pos() stays valid
  virtual bool moving(int column, Axis ax) = 0;
  virtual float pos(int column, Axis ax) = 0;
  virtual bool stalled(int column, Axis ax) = 0;  // StallGuard tripped during the current move
  virtual void hold(int column, bool on) = 0;   // driver enable; lead screws back-drive when off

  // Sensors and actuators.
  virtual uint32_t platformSheets(int column) = 0;  // sheets counted onto the platform since reset
  virtual void resetPlatform(int column) = 0;
  virtual bool doorClosed(int lock) = 0;
  virtual bool paperIn(int lock) = 0;
  virtual void unlock(int lock, uint32_t pulseMs) = 0;  // energise the solenoid for pulseMs, non-blocking

  virtual void poll() {}                        // called every loop: timers, LEDs, sensor debouncing
};
