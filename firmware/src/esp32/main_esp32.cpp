// PrintLocker controller on the ESP32 DevKit: 1 column (lift + pusher), 8 compartments.
// Pin map and wiring: hardware/README.md. Logic: src/core (shared with the PC build).
#include <Adafruit_MCP23X17.h>
#include <Adafruit_NeoPixel.h>
#include <Arduino.h>
#include <FastAccelStepper.h>
#include <TMCStepper.h>
#include <Wire.h>

#include "../core/controller.h"

namespace pins {
constexpr int LIFT_STEP = 25, LIFT_DIR = 26;     // both lift drivers share STEP/DIR -> screws stay in sync
constexpr int PUSH_STEP = 32, PUSH_DIR = 33;
constexpr int DRV_EN = 13;                       // active low, all three drivers
constexpr int TMC_RX = 16, TMC_TX = 17;          // single-wire UART, addresses 0..2
constexpr int SDA_ = 21, SCL_ = 22;
constexpr int LIFT_BOTTOM = 34, LIFT_TOP = 35;   // input-only pins, external 10k pull-ups, pressed = LOW
constexpr int PUSH_HOME = 39;
constexpr int PLATFORM_SENSOR = 36;              // IR beam under the exit: one falling edge per sheet
constexpr int LEDS = 4;
constexpr int FAN = 18;
}  // namespace pins

namespace cal {  // calibrate on the bench (bring-up guide), then set here
constexpr float LIFT_STEPS_PER_MM = 200 * 16 / 8.0f;   // 1.8 deg motor, 1/16 microstep, T8 lead 8 mm = 400
constexpr float PUSH_STEPS_PER_MM = 200 * 16 / 40.0f;  // GT2 20-tooth pulley = 40 mm/rev -> 80
constexpr float RAMP_S = 0.2f;                         // accel time at each end of a move
constexpr uint16_t LIFT_MA = 900, PUSH_MA = 700;       // motor RMS current
constexpr uint8_t SG_THRESHOLD = 60;                   // StallGuard: stall when SG_RESULT <= 2 x this
constexpr float R_SENSE = 0.11f;
constexpr uint32_t HOMING_TIMEOUT_MS = 30000;
constexpr int N_COMPARTMENTS = 8;
}  // namespace cal

FastAccelStepperEngine engine;
HardwareSerial& tmcSerial = Serial2;
TMC2209Stepper liftL(&tmcSerial, cal::R_SENSE, 0), liftR(&tmcSerial, cal::R_SENSE, 1), pushDrv(&tmcSerial, cal::R_SENSE, 2);
Adafruit_MCP23X17 io1, io2;  // io1 0x20: GPA0-7 lock outputs, GPB0-7 reed switches; io2 0x21: GPA0-7 paper sensors, GPB0 bay door
Adafruit_NeoPixel leds(cal::N_COMPARTMENTS + 30, pins::LEDS, NEO_GRB + NEO_KHZ800);  // 8 door LEDs, then the status strip

volatile uint32_t sheetCount = 0;
volatile uint32_t lastEdgeMs = 0;
void IRAM_ATTR onSheet() {
  uint32_t now = millis();
  if (now - lastEdgeMs > 30) sheetCount = sheetCount + 1;  // debounce
  lastEdgeMs = now;
}

class EspHal : public Hal {
 public:
  void begin() {
    pinMode(pins::DRV_EN, OUTPUT);
    digitalWrite(pins::DRV_EN, HIGH);  // drivers off until needed
    for (int p : {pins::LIFT_BOTTOM, pins::LIFT_TOP, pins::PUSH_HOME, pins::PLATFORM_SENSOR}) pinMode(p, INPUT);
    attachInterrupt(digitalPinToInterrupt(pins::PLATFORM_SENSOR), onSheet, FALLING);

    tmcSerial.begin(115200, SERIAL_8N1, pins::TMC_RX, pins::TMC_TX);
    setupDriver(liftL, cal::LIFT_MA);
    setupDriver(liftR, cal::LIFT_MA);
    setupDriver(pushDrv, cal::PUSH_MA);

    engine.init();
    lift_ = engine.stepperConnectToPin(pins::LIFT_STEP);
    push_ = engine.stepperConnectToPin(pins::PUSH_STEP);
    lift_->setDirectionPin(pins::LIFT_DIR);
    push_->setDirectionPin(pins::PUSH_DIR);

    Wire.begin(pins::SDA_, pins::SCL_);
    ioOk_ = io1.begin_I2C(0x20, &Wire) && io2.begin_I2C(0x21, &Wire);
    for (int i = 0; i < 8; i++) {
      io1.pinMode(i, OUTPUT);
      io1.digitalWrite(i, LOW);
      io1.pinMode(8 + i, INPUT_PULLUP);
      io2.pinMode(i, INPUT_PULLUP);
    }
    io2.pinMode(8, INPUT_PULLUP);

    leds.begin();
    leds.show();
    analogWrite(pins::FAN, 150);
  }

  uint32_t ms() override { return millis(); }
  void send(const char* line) override { Serial.println(line); }

  bool configureLock(int lock, int column, int slot) override {
    if (column != 1 || slot < 1 || slot > cal::N_COMPARTMENTS) return false;
    slotOf_[lock] = slot;
    return true;
  }

  void home(int) override {
    if (homed_) return;
    hold(1, true);
    homeAxis(lift_, pins::LIFT_BOTTOM, cal::LIFT_STEPS_PER_MM, 20);
    lift_->setCurrentPosition(static_cast<int32_t>(geo_.receiveMm * cal::LIFT_STEPS_PER_MM));
    homeAxis(push_, pins::PUSH_HOME, cal::PUSH_STEPS_PER_MM, 30);
    push_->setCurrentPosition(0);
    hold(1, false);
    homed_ = true;
  }

  void moveTo(int, Axis ax, float mm, float speed) override {
    FastAccelStepper* s = stepper(ax);
    float spm = ax == LIFT ? cal::LIFT_STEPS_PER_MM : cal::PUSH_STEPS_PER_MM;
    uint32_t hz = static_cast<uint32_t>(speed * spm);
    s->setSpeedInHz(hz);
    s->setAcceleration(static_cast<int32_t>(hz / cal::RAMP_S));
    stall_[ax] = false;
    moveStart_[ax] = millis();
    s->moveTo(static_cast<int32_t>(mm * spm));
  }
  void stop(int, Axis ax) override { stepper(ax)->stopMove(); }
  bool moving(int, Axis ax) override { return stepper(ax)->isRunning(); }
  float pos(int, Axis ax) override {
    return stepper(ax)->getCurrentPosition() / (ax == LIFT ? cal::LIFT_STEPS_PER_MM : cal::PUSH_STEPS_PER_MM);
  }
  bool stalled(int, Axis ax) override { return stall_[ax]; }
  void hold(int, bool on) override { digitalWrite(pins::DRV_EN, on ? LOW : HIGH); }

  uint32_t platformSheets(int) override { return sheetCount; }
  void resetPlatform(int) override { sheetCount = 0; }
  bool doorClosed(int lock) override { return ioOk_ && io1.digitalRead(8 + pin(lock)) == LOW; }
  bool paperIn(int lock) override { return ioOk_ && io2.digitalRead(pin(lock)) == LOW; }
  void unlock(int lock, uint32_t pulseMs) override {
    io1.digitalWrite(pin(lock), HIGH);
    lockOff_[pin(lock)] = millis() + pulseMs;
  }

  void poll() override {
    uint32_t now = millis();
    for (int i = 0; i < 8; i++)
      if (lockOff_[i] && static_cast<int32_t>(now - lockOff_[i]) >= 0) {
        io1.digitalWrite(i, LOW);  // solenoids must never stay energised
        lockOff_[i] = 0;
      }
    if (now - lastSg_ >= 50) {  // StallGuard: only at cruise speed, not during the start ramp
      lastSg_ = now;
      checkStall(LIFT, liftL);
      checkStall(LIFT, liftR);
      checkStall(PUSHER, pushDrv);
    }
    if (now - lastLed_ >= 200) {
      lastLed_ = now;
      for (auto& kv : slotOf_) {
        int i = kv.second - 1;
        bool open = !doorClosed(kv.first), paper = paperIn(kv.first);
        leds.setPixelColor(i, open ? leds.Color(255, 140, 0) : paper ? leds.Color(0, 160, 40) : 0);
      }
      leds.show();
    }
  }

 private:
  static void setupDriver(TMC2209Stepper& d, uint16_t ma) {
    d.begin();
    d.toff(4);
    d.rms_current(ma);
    d.microsteps(16);
    d.pwm_autoscale(true);
    d.en_spreadCycle(false);   // StealthChop: quiet, and StallGuard4 works in it
    d.TCOOLTHRS(0xFFFFF);
    d.SGTHRS(cal::SG_THRESHOLD);
  }
  void homeAxis(FastAccelStepper* s, int limitPin, float spm, float mmPerS) {
    if (digitalRead(limitPin) == LOW) return;
    s->setSpeedInHz(static_cast<uint32_t>(mmPerS * spm));
    s->setAcceleration(static_cast<int32_t>(mmPerS * spm * 5));
    s->runBackward();
    uint32_t t0 = millis();
    while (digitalRead(limitPin) != LOW && millis() - t0 < cal::HOMING_TIMEOUT_MS) delay(1);
    s->forceStop();
    if (digitalRead(limitPin) != LOW) send("{\"event\":\"homing_failed\"}");
  }
  void checkStall(Axis ax, TMC2209Stepper& d) {
    FastAccelStepper* s = stepper(ax);
    if (!s->isRunning() || millis() - moveStart_[ax] < 400) return;
    if (d.SG_RESULT() <= 2 * cal::SG_THRESHOLD) {
      s->forceStop();
      stall_[ax] = true;
    }
  }
  FastAccelStepper* stepper(Axis ax) { return ax == LIFT ? lift_ : push_; }
  int pin(int lock) {
    auto it = slotOf_.find(lock);
    return it == slotOf_.end() ? 0 : it->second - 1;
  }

  Geometry geo_;
  FastAccelStepper* lift_ = nullptr;
  FastAccelStepper* push_ = nullptr;
  bool ioOk_ = false, homed_ = false;
  bool stall_[2] = {false, false};
  uint32_t moveStart_[2] = {0, 0};
  uint32_t lockOff_[8] = {0};
  uint32_t lastSg_ = 0, lastLed_ = 0;
  std::map<int, int> slotOf_;
};

EspHal hal;
Controller controller(hal);
std::string line;

void setup() {
  Serial.begin(115200);
  hal.begin();
  controller.begin();
}

void loop() {
  while (Serial.available()) {
    char c = static_cast<char>(Serial.read());
    if (c == '\n') {
      controller.onLine(line);
      line.clear();
    } else if (c != '\r' && line.size() < 2048) {
      line += c;
    }
  }
  controller.tick();
}
