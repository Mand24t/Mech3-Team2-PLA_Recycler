
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

// Initial conservative speed limit
const float MAX_STEPPER_RPM = 20.0;

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
float servoSpeed = 30.0;       // commanded degrees per second
float servoBounds = 0.0;      // symmetric +/- degrees about center
float servoPosition = 0.0;
int servoDirection = 1;
bool servoRunning = false;
unsigned long servoLastUpdateMs = 0;


int fanPwm = 0;
int sparkPercent = 0;

bool sparkArmed = false;

unsigned long lastContact = 0;

char input[80];
byte inputLength = 0;

// ========================================
// STEPPER CONTROL
// ========================================

void setStepper(StepChannel &m, float rpm) {
  rpm = constrain(
    rpm, -MAX_STEPPER_RPM, MAX_STEPPER_RPM
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
  servoRunning = false;
  if (sweepServo.attached()) sweepServo.detach();
}

void startSweepServo(float center, float speed, float bounds) {
  stopSweepServo();

  servoCenter = center;
  servoSpeed = speed;
  servoBounds = bounds;
  servoPosition = center;
  servoDirection = 1;
  servoLastUpdateMs = millis();

  // Command center first, then enable servo pulses.
  sweepServo.write(constrain((int)round(90.0 + center), 0, 180));
  sweepServo.attach(SWEEP_SERVO_PIN);
  servoRunning = true;
}

void tickSweepServo() {
  if (!servoRunning) return;

  unsigned long now = millis();
  unsigned long elapsedMs = now - servoLastUpdateMs;
  if (elapsedMs < 20) return;
  servoLastUpdateMs = now;

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
  int servoAngle = constrain((int)round(90.0 + servoPosition), 0, 180);
  sweepServo.write(servoAngle);
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
  Serial.println(
    CL_HARDWARE_READY ? 1 : 0
  );
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

    if (fabs(value) > MAX_STEPPER_RPM) {
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
