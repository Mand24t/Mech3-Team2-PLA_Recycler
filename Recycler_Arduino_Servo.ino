
#include <Arduino.h>
#include <Servo.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>

// ========================================
// RECYCLER - ARDUINO MEGA RPM CONTROLLER
// ========================================

// Pin assignments
const byte CL_STEP = 30;
const byte CL_DIR = 31;

const byte DM_STEP = 40;
const byte DM_DIR = 41;

const byte FAN_IN1 = 6;
const byte FAN_IN2 = 7;

const byte SPARK_PIN = 9;
const byte SWEEP_SERVO_PIN = 10;

// Match pulses/revolution to driver switches
const float CL_PPR = 1600.0;
const float DM_PPR = 1600.0;

// Separate motor limits: CL42T puller and DM spooler stepper.
const float MAX_PULLER_RPM = 100.0;
const float MAX_DM_RPM = 20.0;

// Communication watchdog
const unsigned long WATCHDOG_MS = 2500;

// CL42T disabled until hardware fault is fixed
const bool CL_HARDWARE_READY = true;

struct StepChannel {
  byte stepPin;
  byte dirPin;
  float ppr;
  float rpm;
  unsigned long halfPeriodUs;
  unsigned long lastToggleUs;
  bool high;
  unsigned long emittedSteps;
};

StepChannel cl = {
  CL_STEP, CL_DIR, CL_PPR, 0, 0, 0, false
};

StepChannel dm = {
  DM_STEP, DM_DIR, DM_PPR, 0, 0, 0, false
};

Servo spark;
Servo sweepServo;

// Servo is intentionally not attached until SERVO command arrives.
// Angles use centered coordinates: -90..+90 (0 = midpoint).
float servoCenter = 0.0;
float servoSpeed = 0.25;      // commanded degrees per second
float servoBounds = 31.0;     // symmetric +/- degrees about center
float servoPosition = 0.0;
float servoStart = 31.0;
int servoDirection = 1;
bool servoRunning = false;
bool servoPaused = false;
bool servoCentered = false;
bool manualSweepConfigured = false;
unsigned long servoLastUpdateMs = 0;

// Automatic directioner: n counts commanded -limit -> +limit -> -limit trips.
// No encoder/limit sensor is fitted; these are commanded, not measured, angles.
const float AUTO_LIMIT_DEG = 31.36;
bool autoSweep = false;
bool oscillatorTared = false;
bool visitedUpper = false;
unsigned long fullCycles = 0;
float autoVelocity = 0.0; // estimated filament velocity, mm/s
float autoGain = 1.0;

// Independent, RPM-synchronised linear level-winding mode.
bool linearSweep = false;
bool windingReference = false;
unsigned long windingLayers = 0;
float windingCore = 87.0;
float windingWidth = 55.0;
float windingFilament = 1.75;
float windingMin = -31.0;
float windingMax = 31.0;
float windingMotorRatio = 1.0;
unsigned long windingStepReference = 0;


int fanPwm = 0;
int sparkPercent = 0;

bool sparkArmed = false;

unsigned long lastContact = 0;

char input[128];
byte inputLength = 0;

// ========================================
// STEPPER CONTROL
// ========================================

void setStepper(StepChannel &m, float rpm) {
  const float limit = m.stepPin == CL_STEP ? MAX_PULLER_RPM : MAX_DM_RPM;
  rpm = constrain(
    rpm, -limit, limit
  );

  digitalWrite(m.stepPin, LOW);
  m.high = false;
  m.rpm = rpm;

  if (rpm == 0) {
    m.halfPeriodUs = 0;
    return;
  }

  digitalWrite(
    m.dirPin, rpm >= 0 ? HIGH : LOW
  );

  float pulseRate =
    fabs(rpm) * m.ppr / 60.0;

  m.halfPeriodUs =
    (unsigned long)(500000.0 / pulseRate);

  if (m.halfPeriodUs < 10) {
    m.halfPeriodUs = 10;
  }

  m.lastToggleUs = micros();
}

void tickStepper(StepChannel &m) {
  if (m.halfPeriodUs == 0) return;

  unsigned long now = micros();

  if ((unsigned long)(
      now - m.lastToggleUs
      ) >= m.halfPeriodUs) {

    m.high = !m.high;
    if (m.high) ++m.emittedSteps;

    digitalWrite(
      m.stepPin, m.high ? HIGH : LOW
    );

    m.lastToggleUs = now;
  }
}

// ========================================
// FAN CONTROL
// ========================================

void setFan(int pwm) {
  fanPwm = constrain(pwm, 0, 255);

  digitalWrite(FAN_IN2, LOW);
  analogWrite(FAN_IN1, fanPwm);
}

// ========================================
// SPARK MAX CONTROL
// ========================================

void setSpark(int percent) {
  sparkPercent = constrain(
    percent, -100, 100
  );

  // Assumes RC PWM mode with 1500us neutral
  spark.writeMicroseconds(
    1500 + sparkPercent * 5
  );
}

// ========================================
// D10 SWEEP SERVO CONTROL (NON-BLOCKING)
// ========================================

void stopSweepServo() {
  autoSweep = false;
  linearSweep = false;
  servoRunning = false;
  servoPaused = servoCentered = manualSweepConfigured = false;
  if (sweepServo.attached()) sweepServo.detach();
}

void pauseSweepServo() {
  if (servoRunning && !servoPaused) tickSweepServo();
  servoPaused = true;
  servoCentered = false;
  servoLastUpdateMs = millis();
}

void centerSweepServo() {
  if (servoRunning && !servoPaused) tickSweepServo();
  servoPaused = servoCentered = true;
  servoPosition = 0;
  if (windingReference && (0 < windingMin || 0 > windingMax)) windingReference = false;
  writeAutomaticPosition();
  if (!sweepServo.attached()) sweepServo.attach(SWEEP_SERVO_PIN);
  servoRunning = true;
  servoLastUpdateMs = millis();
}

float windingAngularSpeed() {
  return fabs(dm.rpm) / windingMotorRatio / 60.0 * windingFilament *
         (windingMax - windingMin) / (windingWidth - windingFilament);
}

float windingRadius() {
  return windingCore / 2.0 + windingLayers * windingFilament;
}

void tareWinding() {
  stopSweepServo();
  oscillatorTared = false;
  windingReference = true;
  windingLayers = fullCycles = 0;
  windingStepReference = dm.emittedSteps;
  visitedUpper = false;
  servoPosition = servoCenter = windingMin;
  servoBounds = servoSpeed = 0;
  servoDirection = 1;
  servoLastUpdateMs = millis();
  writeAutomaticPosition();
  if (!sweepServo.attached()) sweepServo.attach(SWEEP_SERVO_PIN);
  servoRunning = true;
}

void tickLinearWinding() {
  unsigned long steps = dm.emittedSteps - windingStepReference;
  windingStepReference = dm.emittedSteps;
  // Synchronise to emitted spooler STEP edges, not GUI timing or ideal wall
  // time. This lays one pitch per commanded spool revolution even if loop
  // timing varies. Actual motor slip still requires physical verification.
  float remaining = steps / dm.ppr / windingMotorRatio * windingFilament *
                    (windingMax - windingMin) / (windingWidth - windingFilament);
  for (byte transitions = 0; transitions < 8 && remaining > 0; ++transitions) {
    float end = servoDirection > 0 ? windingMax : windingMin;
    float distance = fabs(end - servoPosition);
    if (remaining < distance) {
      servoPosition += servoDirection * remaining;
      remaining = 0;
    } else {
      servoPosition = end;
      remaining -= distance;
      if (windingLayers < 0xFFFFFFFFUL) ++windingLayers; // New layer at EACH end.
      if (servoDirection > 0) {
        visitedUpper = true;
        servoDirection = -1;
      } else {
        if (visitedUpper && fullCycles < 0xFFFFFFFFUL) ++fullCycles;
        visitedUpper = false;
        servoDirection = 1;
      }
    }
  }
  servoSpeed = windingAngularSpeed();
  writeAutomaticPosition();
}

void writeAutomaticPosition() {
  // Fractional-degree commands using the same default Servo pulse mapping as
  // attach(pin). Physical angle still depends on the installed servo calibration.
  float angle = constrain(90.0 + servoPosition, 0.0, 180.0);
  int pulse = (int)round(MIN_PULSE_WIDTH + angle *
                        (MAX_PULSE_WIDTH - MIN_PULSE_WIDTH) / 180.0);
  sweepServo.writeMicroseconds(pulse);
}

float automaticAngularSpeed() {
  if (!autoSweep || autoVelocity <= 0 || autoGain <= 0) return 0;
  float spoolDiameter = 87.0 + 3.5 * fullCycles;
  float radiansPerSecond = autoGain * 1.75 * autoVelocity /
    (PI * spoolDiameter * 55.0 * cos(servoPosition * PI / 180.0));
  return radiansPerSecond * 180.0 / PI;
}

void tareOscillator() {
  stopSweepServo();
  windingReference = false;
  servoPosition = -AUTO_LIMIT_DEG;
  servoCenter = servoPosition;
  servoBounds = 0;
  servoSpeed = 0;
  servoDirection = 1;
  visitedUpper = false;
  fullCycles = 0;
  oscillatorTared = true;
  writeAutomaticPosition();
  if (!sweepServo.attached()) sweepServo.attach(SWEEP_SERVO_PIN);
  servoRunning = true; // hold at the endpoint; TARE does not start oscillating.
  servoLastUpdateMs = millis();
}

void tickAutomaticSweep(float remainingSeconds) {
  // d(theta)/dt = C/cos(theta), so d(sin(theta))/dt = C. Integrate in
  // sin(theta) exactly for each leg, carrying overshoot through reflections.
  const float limit = sin(AUTO_LIMIT_DEG * PI / 180.0);
  float position = sin(servoPosition * PI / 180.0);
  for (byte transitions = 0; transitions < 8 && remainingSeconds > 0; ++transitions) {
    float rate = autoGain * 1.75 * autoVelocity /
      (PI * (87.0 + 3.5 * fullCycles) * 55.0);
    if (rate <= 0) break;
    float endpoint = servoDirection > 0 ? limit : -limit;
    float timeToEnd = fabs(endpoint - position) / rate;
    if (remainingSeconds < timeToEnd) {
      position += servoDirection * rate * remainingSeconds;
      remainingSeconds = 0;
    } else {
      position = endpoint;
      remainingSeconds -= timeToEnd;
      if (servoDirection > 0) {
        visitedUpper = true;
        servoDirection = -1;
      } else {
        if (visitedUpper && fullCycles < 0xFFFFFFFFUL) ++fullCycles;
        visitedUpper = false;
        servoDirection = 1;
      }
    }
  }
  servoPosition = asin(constrain(position, -limit, limit)) * 180.0 / PI;
  servoPosition = constrain(servoPosition, -AUTO_LIMIT_DEG, AUTO_LIMIT_DEG);
  servoSpeed = automaticAngularSpeed();
  writeAutomaticPosition();
}

void beginSweepServo(float center, float speed, float bounds, float startingAngle) {
  stopSweepServo();
  oscillatorTared = false;
  windingReference = false;
  servoCenter = center;
  servoSpeed = speed;
  servoBounds = bounds;
  servoStart = startingAngle;
  servoPosition = startingAngle;
  // A positive-side start heads toward the negative limit, and vice versa.
  servoDirection = startingAngle > center ? -1 : 1;
  servoLastUpdateMs = millis();
  writeAutomaticPosition();
  sweepServo.attach(SWEEP_SERVO_PIN);
  servoRunning = true;
  manualSweepConfigured = true;
}

void continueSweepServo(float center, float speed, float bounds, float startingAngle) {
  // Apply parameters at the parked current angle, retaining the travel direction.
  autoSweep = linearSweep = oscillatorTared = windingReference = false;
  servoCenter = center; servoSpeed = speed; servoBounds = bounds; servoStart = startingAngle;
  servoPaused = servoCentered = false;
  manualSweepConfigured = servoRunning = true;
  servoLastUpdateMs = millis();
  writeAutomaticPosition();
  if (!sweepServo.attached()) sweepServo.attach(SWEEP_SERVO_PIN);
}

void startSweepServo(float center, float speed, float bounds) {
  // A live speed-only update must not detach, re-center or reverse the sweep.
  // Advance elapsed motion at the old speed before applying the new speed.
  if (servoRunning && sweepServo.attached() && !autoSweep && !linearSweep && !oscillatorTared && !windingReference &&
      fabs(center - servoCenter) < 0.001 && fabs(bounds - servoBounds) < 0.001) {
    tickSweepServo();
    servoSpeed = speed;
    return;
  }
  beginSweepServo(center, speed, bounds, center);
}

void tickSweepServo() {
  if (!servoRunning) return;
  if (servoPaused) {
    writeAutomaticPosition();
    return;
  }

  unsigned long now = millis();
  unsigned long elapsedMs = now - servoLastUpdateMs;
  if (elapsedMs < 20) return;
  servoLastUpdateMs = now;

  if (linearSweep) {
    tickLinearWinding();
    return;
  }

  if (autoSweep) {
    // Do not claim uncommanded travel if the main loop was delayed.
    tickAutomaticSweep(min(elapsedMs, 200UL) / 1000.0);
    return;
  }
  if ((oscillatorTared || windingReference) && servoBounds == 0) {
    writeAutomaticPosition(); // Preserve fractional degrees while tared/paused.
    return;
  }

  float low = servoCenter - servoBounds;
  float high = servoCenter + servoBounds;
  float remaining = servoSpeed * (elapsedMs / 1000.0);

  // Reflect at the boundaries, including when an iteration overshoots.
  // Limit to a bounded iteration count to protect the main loop.
  if (servoBounds <= 0.0) {
    servoPosition = servoCenter;
  } else {
    float width = high - low;
    float phase = (servoDirection > 0)
      ? (servoPosition - low)
      : (2.0 * width - (servoPosition - low));
    phase = fmod(phase + remaining, 2.0 * width);
    if (phase <= width) {
      servoPosition = low + phase;
      servoDirection = (phase == width) ? -1 : 1;
    } else {
      servoPosition = high - (phase - width);
      servoDirection = -1;
    }
  }

  // 0..180 is the standard Servo library API; never command beyond
  // -90..+90 relative to the assumed 90-degree midpoint.
  writeAutomaticPosition(); // Preserve fractional movement at slow sweep rates.
}

// ========================================
// STOP ALL
// ========================================

void stopAll() {
  setStepper(cl, 0);
  setStepper(dm, 0);
  setFan(0);
  setSpark(0);
  stopSweepServo();

  sparkArmed = false;
}

// ========================================
// STATUS
// ========================================

void printStatus() {
  Serial.print(F("STATUS CL="));
  Serial.print(cl.rpm, 2);

  Serial.print(F(" DM="));
  Serial.print(dm.rpm, 2);

  Serial.print(F(" FAN="));
  Serial.print(fanPwm);

  Serial.print(F(" SPARK="));
  Serial.print(sparkPercent);

  Serial.print(F(" ARM="));
  Serial.print(sparkArmed ? 1 : 0);

  Serial.print(F(" SERVO="));
  Serial.print(servoRunning ? 1 : 0);

  Serial.print(F(" SCENTER="));
  Serial.print(servoCenter, 1);

  Serial.print(F(" SSPEED="));
  Serial.print(servoSpeed, 1);

  Serial.print(F(" SBOUNDS="));
  Serial.print(servoBounds, 1);

  Serial.print(F(" CL_READY="));
  Serial.print(
    CL_HARDWARE_READY ? 1 : 0
  );
  Serial.print(F(" OSC_V=1 N="));
  Serial.print(fullCycles);
  Serial.print(F(" THETA="));
  Serial.print(servoPosition, 4);
  Serial.print(F(" OMEGA="));
  Serial.print(servoPaused ? 0 : linearSweep ? windingAngularSpeed() : autoSweep ? automaticAngularSpeed() :
               (servoRunning && servoBounds > 0 ? servoSpeed : 0), 6);
  Serial.print(F(" TARED="));
  Serial.print(oscillatorTared ? 1 : 0);
  Serial.print(F(" OSC_AUTO="));
  Serial.print(autoSweep ? 1 : 0);
  Serial.print(F(" SWEEP_V=1 SSTART="));
  Serial.print(servoStart, 4);
  Serial.print(F(" WIND_V=1 LAYERS="));
  Serial.print(windingLayers);
  Serial.print(F(" RADIUS="));
  Serial.print(windingRadius(), 4);
  Serial.print(F(" WIND_READY="));
  Serial.print(windingReference ? 1 : 0);
  Serial.print(F(" WIND_ACTIVE="));
  Serial.print(linearSweep && !servoPaused ? 1 : 0);
  Serial.print(F(" ANGLE_V=1 PAUSE_V=1 S_PAUSED="));
  Serial.print(servoPaused ? 1 : 0);
  Serial.print(F(" S_CENTERED="));
  Serial.println(servoCentered ? 1 : 0);
}

// ========================================
// COMMAND PARSING
// ========================================

bool parseNumber(char *s, float &value) {
  if (!s || !*s) return false;

  char *end;
  value = strtod(s, &end);

  return end != s &&
         *end == '\0' &&
         isfinite(value);
}

void processCommand(char *line) {
  char *arg = strchr(line, ' ');

  if (arg) {
    *arg = '\0';
    ++arg;
  }

  if (!strcmp(line, "PING")) {
    Serial.println(F("PONG"));
    return;
  }

  if (!strcmp(line, "STATUS")) {
    printStatus();
    return;
  }

  if (!strcmp(line, "STOP")) {
    stopAll();
    Serial.println(F("OK STOP"));
    return;
  }

  if (!strcmp(line, "ARM")) {
    setSpark(0);
    sparkArmed = true;
    Serial.println(F("OK ARM"));
    return;
  }

  if (!strcmp(line, "SERVOSTOP")) {
    stopSweepServo();
    Serial.println(F("OK SERVOSTOP"));
    return;
  }

  if (!strcmp(line, "SWEEPANGLE")) {
    float target, hold;
    char *first = arg ? strtok(arg, " \t") : NULL;
    char *second = first ? strtok(NULL, " \t") : NULL;
    if (!first || !second || !parseNumber(first, target) || !parseNumber(second, hold) ||
        strtok(NULL, " \t") || target < -90 || target > 90 || (hold != 0 && hold != 1)) {
      Serial.println(F("ERR SWEEP_ANGLE"));
      return;
    }
    float low = -90, high = 90;
    if (linearSweep) { low = windingMin; high = windingMax; }
    else if (autoSweep) { low = -AUTO_LIMIT_DEG; high = AUTO_LIMIT_DEG; }
    else if (manualSweepConfigured) { low = servoCenter-servoBounds; high = servoCenter+servoBounds; }
    if (target < low-0.02 || target > high+0.02) {
      Serial.println(F("ERR SWEEP_ANGLE_LIMITS"));
      return;
    }
    bool wasRunning = servoRunning;
    if (servoRunning && !servoPaused) tickSweepServo();
    servoPosition = constrain(target, low, high);
    if (servoPosition <= low) servoDirection = 1;
    else if (servoPosition >= high) servoDirection = -1;
    writeAutomaticPosition();
    if (!sweepServo.attached()) sweepServo.attach(SWEEP_SERVO_PIN);
    servoRunning = true;
    if (hold == 1 || !wasRunning) servoPaused = true;
    servoCentered = hold == 1;
    if (windingReference && (servoPosition < windingMin || servoPosition > windingMax)) windingReference = false;
    // A commanded reposition is not a layer or winding travel.
    servoLastUpdateMs = millis();
    windingStepReference = dm.emittedSteps;
    Serial.println(F("OK SWEEPANGLE"));
    return;
  }

  if (!strcmp(line, "SWEEPPAUSE")) {
    if (!servoRunning || !sweepServo.attached()) {
      Serial.println(F("ERR SWEEP_NOT_RUNNING"));
      return;
    }
    pauseSweepServo();
    Serial.println(F("OK SWEEPPAUSE"));
    return;
  }
  if (!strcmp(line, "SWEEPCENTER")) {
    centerSweepServo();
    Serial.println(F("OK SWEEPCENTER"));
    return;
  }
  if (!strcmp(line, "SWEEPRESUME")) {
    if (!servoRunning || !servoPaused || !sweepServo.attached() ||
        (!manualSweepConfigured && !linearSweep && !autoSweep)) {
      Serial.println(F("ERR_SWEEP_NOT_PAUSED"));
      return;
    }
    if (linearSweep && !windingReference) {
      Serial.println(F("ERR WIND_TARE_REQUIRED"));
      return;
    }
    servoPaused = servoCentered = false;
    servoLastUpdateMs = millis();
    windingStepReference = dm.emittedSteps;
    Serial.println(F("OK SWEEPRESUME"));
    return;
  }

  if (!strcmp(line, "WINDCFG")) {
    float values[6];
    char *token = arg ? strtok(arg, " \t") : NULL;
    bool valid = true;
    for (byte i = 0; i < 6; ++i) {
      if (!token || !parseNumber(token, values[i])) { valid = false; break; }
      token = strtok(NULL, " \t");
    }
    if (!valid || token || values[0] <= 0 || values[2] <= 0 || values[1] <= values[2] ||
        values[3] < -90 || values[4] > 90 || values[3] >= values[4] || values[5] <= 0) {
      Serial.println(F("ERR WIND_GEOMETRY"));
      return;
    }
    float maxRate = MAX_DM_RPM / values[5] / 60.0 * values[2] *
                    (values[4] - values[3]) / (values[1] - values[2]);
    if (!isfinite(maxRate) || maxRate > 360) {
      Serial.println(F("ERR WIND_SPEED"));
      return;
    }
    bool changed = values[0] != windingCore || values[1] != windingWidth ||
      values[2] != windingFilament || values[3] != windingMin ||
      values[4] != windingMax || values[5] != windingMotorRatio;
    if (changed) {
      // Recalibrating the mapping requires an explicit new layer reference.
      stopSweepServo();
      windingReference = false;
    }
    windingCore = values[0]; windingWidth = values[1]; windingFilament = values[2];
    windingMin = values[3]; windingMax = values[4]; windingMotorRatio = values[5];
    Serial.println(F("OK WINDCFG"));
    return;
  }
  if (!strcmp(line, "WINDTARE")) {
    tareWinding();
    Serial.println(F("OK WINDTARE"));
    return;
  }
  if (!strcmp(line, "WINDSTART")) {
    if (!windingReference) {
      Serial.println(F("ERR WIND_TARE_REQUIRED"));
      return;
    }
    if (!sweepServo.attached()) {
      writeAutomaticPosition();
      sweepServo.attach(SWEEP_SERVO_PIN);
    }
    bool resuming = !linearSweep || servoPaused;
    autoSweep = false;
    linearSweep = servoRunning = true;
    servoPaused = servoCentered = false;
    if (resuming) {
      servoLastUpdateMs = millis();
      windingStepReference = dm.emittedSteps;
    }
    Serial.println(F("OK WINDSTART"));
    return;
  }
  if (!strcmp(line, "WINDPAUSE")) {
    if (servoRunning) pauseSweepServo();
    Serial.println(F("OK WINDPAUSE"));
    return;
  }

  if (!strcmp(line, "TARE")) {
    tareOscillator();
    Serial.println(F("OK TARE"));
    return;
  }

  if (!strcmp(line, "AUTOPAUSE")) {
    if (servoRunning) pauseSweepServo();
    Serial.println(F("OK AUTOPAUSE"));
    return;
  }

  if (!strcmp(line, "AUTOSWEEP")) {
    char *velocityArg = arg ? strtok(arg, " \t") : NULL;
    char *gainArg = velocityArg ? strtok(NULL, " \t") : NULL;
    char *extraArg = gainArg ? strtok(NULL, " \t") : NULL;
    float velocity, gain;
    if (!velocityArg || !gainArg || extraArg ||
        !parseNumber(velocityArg, velocity) || !parseNumber(gainArg, gain) ||
        velocity < 0 || velocity > 1000 || gain < 0) {
      Serial.println(F("ERR AUTOSWEEP_ARGS"));
      return;
    }
    float maxOmega = gain * 1.75 * velocity /
      (PI * 87.0 * 55.0 * cos(AUTO_LIMIT_DEG * PI / 180.0)) * 180.0 / PI;
    if (!isfinite(maxOmega) || maxOmega > 360) {
      Serial.println(F("ERR AUTO_SPEED_RANGE"));
      return;
    }
    if (!oscillatorTared) {
      Serial.println(F("ERR TARE_REQUIRED"));
      return;
    }
    bool resuming = !autoSweep || servoPaused;
    if (!sweepServo.attached()) {
      writeAutomaticPosition();
      sweepServo.attach(SWEEP_SERVO_PIN);
    }
    tickSweepServo();
    autoVelocity = velocity;
    autoGain = gain;
    autoSweep = true;
    linearSweep = false;
    windingReference = false;
    servoRunning = true;
    servoPaused = servoCentered = false;
    if (resuming) servoLastUpdateMs = millis();
    Serial.println(F("OK AUTOSWEEP"));
    return;
  }

  if (!strcmp(line, "SWEEPSPEED")) {
    float speed;
    if (!parseNumber(arg, speed) || speed <= 0 || speed > 360) {
      Serial.println(F("ERR SERVO_SPEED"));
      return;
    }
    if (!servoRunning || !sweepServo.attached() || autoSweep || linearSweep || oscillatorTared || windingReference) {
      Serial.println(F("ERR MANUAL_SWEEP_NOT_RUNNING"));
      return;
    }
    tickSweepServo();
    servoSpeed = speed;
    Serial.println(F("OK SWEEPSPEED"));
    return;
  }

  if (!strcmp(line, "SWEEP") || !strcmp(line, "SWEEPCONT")) {
    bool continuing = !strcmp(line, "SWEEPCONT");
    char *centerArg = arg ? strtok(arg, " \t") : NULL;
    char *speedArg = centerArg ? strtok(NULL, " \t") : NULL;
    char *boundsArg = speedArg ? strtok(NULL, " \t") : NULL;
    char *startArg = boundsArg ? strtok(NULL, " \t") : NULL;
    char *extraArg = startArg ? strtok(NULL, " \t") : NULL;
    float center, speed, bounds, startingAngle;
    if (!centerArg || !speedArg || !boundsArg || !startArg || extraArg ||
        !parseNumber(centerArg, center) || !parseNumber(speedArg, speed) ||
        !parseNumber(boundsArg, bounds) || !parseNumber(startArg, startingAngle)) {
      Serial.println(F("ERR SWEEP_ARGS"));
      return;
    }
    if (speed <= 0 || speed > 360 || bounds < 0 || bounds > 90 ||
        center - bounds < -90 || center + bounds > 90 ||
        startingAngle < center - bounds || startingAngle > center + bounds) {
      Serial.println(F("ERR SERVO_RANGE"));
      return;
    }
    if (continuing) {
      if (servoPosition < center - bounds || servoPosition > center + bounds) {
        Serial.println(F("ERR SERVO_CURRENT_ANGLE_OUTSIDE_RANGE"));
        return;
      }
      continueSweepServo(center, speed, bounds, startingAngle);
      Serial.println(F("OK SWEEPCONT"));
    } else {
      beginSweepServo(center, speed, bounds, startingAngle);
      Serial.println(F("OK SWEEP"));
    }
    return;
  }

  if (!strcmp(line, "SERVO")) {
    // SERVO <center_deg> <speed_deg_per_sec> <half_range_deg>
    char *centerArg = arg ? strtok(arg, " \t") : NULL;
    char *speedArg = centerArg ? strtok(NULL, " \t") : NULL;
    char *boundsArg = speedArg ? strtok(NULL, " \t") : NULL;
    char *extraArg = boundsArg ? strtok(NULL, " \t") : NULL;
    float center, speed, bounds;

    if (!centerArg || !speedArg || !boundsArg || extraArg ||
        !parseNumber(centerArg, center) ||
        !parseNumber(speedArg, speed) ||
        !parseNumber(boundsArg, bounds)) {
      Serial.println(F("ERR SERVO_ARGS"));
      return;
    }
    if (center < -90 || center > 90 ||
        speed <= 0 || speed > 360 ||
        bounds < 0 || bounds > 90 ||
        center - bounds < -90 || center + bounds > 90) {
      Serial.println(F("ERR SERVO_RANGE"));
      return;
    }

    startSweepServo(center, speed, bounds);
    Serial.println(F("OK SERVO"));
    return;
  }

  float value;

  if (!parseNumber(arg, value)) {
    Serial.println(F("ERR NUMBER"));
    return;
  }

  if (!strcmp(line, "DMRPM") ||
      !strcmp(line, "CLRPM")) {

    const float limit = !strcmp(line, "CLRPM") ? MAX_PULLER_RPM : MAX_DM_RPM;
    if (fabs(value) > limit) {
      Serial.println(F("ERR RPM_LIMIT"));
      return;
    }

    if (!strcmp(line, "CLRPM")) {

      if (!CL_HARDWARE_READY && value != 0) {
        Serial.println(
          F("ERR CL_FAULT_LOCKOUT")
        );
        return;
      }

      setStepper(cl, value);
      Serial.println(F("OK CLRPM"));

    } else {
      setStepper(dm, value);
      Serial.println(F("OK DMRPM"));
    }
  }

  else if (!strcmp(line, "FAN")) {

    if (value < 0 ||
        value > 255 ||
        value != floor(value)) {
      Serial.println(F("ERR FAN_RANGE"));
      return;
    }

    setFan((int)value);
    Serial.println(F("OK FAN"));
  }

  else if (!strcmp(line, "SPARK")) {

    if (value < -100 ||
        value > 100 ||
        value != floor(value)) {
      Serial.println(F("ERR SPARK_RANGE"));
      return;
    }

    if (value != 0 && !sparkArmed) {
      Serial.println(
        F("ERR SPARK_NOT_ARMED")
      );
      return;
    }

    setSpark((int)value);
    Serial.println(F("OK SPARK"));
  }

  else {
    Serial.println(F("ERR UNKNOWN"));
  }
}

// ========================================
// SETUP
// ========================================

void setup() {
  pinMode(CL_STEP, OUTPUT);
  pinMode(CL_DIR, OUTPUT);

  pinMode(DM_STEP, OUTPUT);
  pinMode(DM_DIR, OUTPUT);

  pinMode(FAN_IN1, OUTPUT);
  pinMode(FAN_IN2, OUTPUT);

  digitalWrite(CL_STEP, LOW);
  digitalWrite(DM_STEP, LOW);

  digitalWrite(FAN_IN1, LOW);
  digitalWrite(FAN_IN2, LOW);

  spark.attach(
    SPARK_PIN, 1000, 2000
  );

  stopAll();

  Serial.begin(115200);

  lastContact = millis();

  Serial.println(
    F("RECYCLER_READY_V2")
  );
}

// ========================================
// MAIN LOOP
// ========================================

void loop() {
  tickStepper(cl);
  tickStepper(dm);
  tickSweepServo();

  while (Serial.available()) {
    char c = (char)Serial.read();

    if (c == '\n') {
      input[inputLength] = '\0';

      if (inputLength) {
        lastContact = millis();
        processCommand(input);
      }

      inputLength = 0;
    }

    else if (c != '\r') {
      if (inputLength < sizeof(input) - 1) {
        input[inputLength++] = c;
      } else {
        inputLength = 0;
      }
    }
  }

  if ((unsigned long)(
      millis() - lastContact
      ) > WATCHDOG_MS) {

    if (cl.rpm != 0 ||
        dm.rpm != 0 ||
        fanPwm ||
        sparkPercent ||
        sparkArmed ||
        servoRunning) {

      stopAll();

      Serial.println(
        F("WATCHDOG_STOP")
      );
    }
  }
}
