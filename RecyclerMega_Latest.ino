#include <Servo.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>

// PLA Recycler - Arduino Mega 2560
// NEO/SPARK MAX: D9 (servo-style PWM), DRV8871 fans: D6/D7
// CL42T: STEP D30, DIR D31 (5V-compatible inputs)
// HPS-2027 positional servo: D10 (external rated supply)
// Requires matching RecyclerControl_Latest.py

const byte EXTRUDER_PIN = 9;
const byte FAN_IN1 = 6, FAN_IN2 = 7;
const byte STEP_PIN = 30, DIR_PIN = 31;
const byte GUIDE_PIN = 10;
const float MOTOR_REFERENCE_RPM = 5000.0f; // calibration reference, NOT feedback
const float GEAR_RATIO = 125.0f;
const float MAX_SCREW_RPM = MOTOR_REFERENCE_RPM / GEAR_RATIO; // 40
const float MAX_STEP_RPM = 60.0f;
const float STEP_PULSES_PER_REV = 1600.0f; // verify CL42T setting
const unsigned long STEP_PULSE_HIGH_US = 10;
const unsigned long TIMEOUT_MS = 1200;

// Positional 270-degree guide servo. Actual travel must be verified unloaded.
const float SERVO_HOME_DEG = 135.0f;
const float SERVO_SAFE_MIN_DEG = 10.0f;
const float SERVO_SAFE_MAX_DEG = 260.0f;
const int SERVO_MIN_US = 500, SERVO_MAX_US = 2500;
const unsigned long SERVO_INTERVAL_MS = 20;

Servo spark, guide;
float currentStepperRPM = 0;
unsigned long stepPeriodUs = 0, nextStepUs = 0, stepFallUs = 0;
bool stepHigh = false;

// servoMode: 0 = detached/disabled, 1 = center, 2 = sweep, 3 = hold
byte servoMode = 0;
bool guideAttached = false;
float servoAngle = SERVO_HOME_DEG, servoCenter = SERVO_HOME_DEG;
float servoRange = 10.0f, servoSpeed = 15.0f;
int sweepDirection = 1;
unsigned long lastServoMs = 0;

char lineBuffer[128];
byte linePos = 0;
unsigned long lastCommandMs = 0;
bool timedOut = false;

void setExtruder(float screwRPM) {
  screwRPM = constrain(screwRPM, 0.0f, MAX_SCREW_RPM);
  // Reverse direction already tested on original machine.
  int us = 1500 - (int)(500.0f * (screwRPM * GEAR_RATIO / MOTOR_REFERENCE_RPM) + 0.5f);
  spark.writeMicroseconds(constrain(us, 1000, 1500));
}

void setFans(float percent) {
  percent = constrain(percent, 0.0f, 100.0f);
  digitalWrite(FAN_IN2, LOW);
  if (percent <= 0.0f) digitalWrite(FAN_IN1, LOW);
  else analogWrite(FAN_IN1, (int)(percent * 255.0f / 100.0f + 0.5f));
}

void setStepper(float rpm) {
  rpm = constrain(rpm, -MAX_STEP_RPM, MAX_STEP_RPM);
  // Do not restart pulse schedule when repeated serial frames arrive.
  if (fabsf(rpm - currentStepperRPM) < 0.001f) return;
  currentStepperRPM = rpm;
  digitalWrite(STEP_PIN, LOW);
  stepHigh = false;
  if (fabsf(rpm) < 0.001f) {
    stepPeriodUs = 0;
    return;
  }
  digitalWrite(DIR_PIN, rpm >= 0 ? HIGH : LOW);
  float period = 60000000.0f / (fabsf(rpm) * STEP_PULSES_PER_REV);
  stepPeriodUs = max((unsigned long)(period + 0.5f), STEP_PULSE_HIGH_US + 15UL);
  nextStepUs = micros() + 50UL;
}

void runStepper() {
  if (!stepPeriodUs) return;
  unsigned long now = micros();
  if (stepHigh) {
    if ((long)(now - stepFallUs) >= 0) {
      digitalWrite(STEP_PIN, LOW);
      stepHigh = false;
    }
  } else if ((long)(now - nextStepUs) >= 0) {
    digitalWrite(STEP_PIN, HIGH);
    stepHigh = true;
    stepFallUs = now + STEP_PULSE_HIGH_US;
    nextStepUs = now + stepPeriodUs;
  }
}

void writeGuideAngle(float angle) {
  angle = constrain(angle, SERVO_SAFE_MIN_DEG, SERVO_SAFE_MAX_DEG);
  int us = SERVO_MIN_US + (int)(angle / 270.0f * (SERVO_MAX_US - SERVO_MIN_US) + 0.5f);
  guide.writeMicroseconds(us);
}

bool servoSettingsValid(float range, float speed, float offset) {
  float center = SERVO_HOME_DEG + offset;
  return isfinite(range) && isfinite(speed) && isfinite(offset) &&
         range >= 0 && range <= 125 && speed > 0 && speed <= 180 &&
         center - range >= SERVO_SAFE_MIN_DEG &&
         center + range <= SERVO_SAFE_MAX_DEG;
}

void disableGuide() {
  servoMode = 0;
  if (guideAttached) {
    guide.detach();
    guideAttached = false;
  }
}

void setGuide(byte mode, float range, float speed, float offset) {
  if (mode == 0) { disableGuide(); return; }
  if (mode > 3 || !servoSettingsValid(range, speed, offset)) return;
  float newCenter = SERVO_HOME_DEG + offset;
  bool wasSweeping = (servoMode == 2);
  servoCenter = newCenter;
  servoRange = range;
  servoSpeed = speed;
  if (!guideAttached) {
    // On initial attach, the physical position is unknown. Unload before testing.
    servoAngle = servoCenter;
    guide.attach(GUIDE_PIN, SERVO_MIN_US, SERVO_MAX_US);
    guideAttached = true;
    writeGuideAngle(servoAngle);
  }
  if (mode == 2 && !wasSweeping) sweepDirection = 1;
  servoMode = mode;
}

void runGuide() {
  unsigned long now = millis();
  if (now - lastServoMs < SERVO_INTERVAL_MS) return;
  float dt = (now - lastServoMs) / 1000.0f;
  lastServoMs = now;
  if (!guideAttached || servoMode == 0 || servoMode == 3) return;
  if (dt > 0.1f) dt = 0.1f;
  float goal = servoCenter;
  if (servoMode == 2) goal += sweepDirection > 0 ? servoRange : -servoRange;
  float maxChange = servoSpeed * dt;
  if (servoAngle < goal) servoAngle = fminf(servoAngle + maxChange, goal);
  else if (servoAngle > goal) servoAngle = fmaxf(servoAngle - maxChange, goal);
  writeGuideAngle(servoAngle);
  if (servoMode == 2 && fabsf(servoAngle - goal) < 0.01f) sweepDirection = -sweepDirection;
}

void stopAll(bool detachServo = true) {
  setExtruder(0);
  setFans(0);
  setStepper(0);
  if (detachServo) disableGuide();
  else servoMode = 3;
}

// Serial frame: SET,<screwRPM>,<signedStepperRPM>,<fanPercent>,<servoMode>,<range>,<speed>,<offset>
// Example: SET,5,10,50,0,10,15,0
// STOP disables all channels including servo output.
void processLine(char *line) {
  if (strcmp(line, "STOP") == 0) {
    stopAll(true);
    lastCommandMs = millis();
    timedOut = false;
    return;
  }
  if (strncmp(line, "SET,", 4) != 0) return;
  float data[7];
  char *p = line + 4;
  for (byte i = 0; i < 7; i++) {
    char *end;
    data[i] = strtod(p, &end);
    if (end == p || !isfinite(data[i])) return;
    if (i < 6) {
      if (*end != ',') return;
      p = end + 1;
    } else if (*end != '\0') return;
  }
  if (data[3] != floorf(data[3]) || data[3] < 0 || data[3] > 3) return;
  if (!servoSettingsValid(data[4], data[5], data[6])) return;
  setExtruder(data[0]);
  setStepper(data[1]);
  setFans(data[2]);
  setGuide((byte)data[3], data[4], data[5], data[6]);
  lastCommandMs = millis();
  timedOut = false;
}

void setup() {
  pinMode(FAN_IN1, OUTPUT); pinMode(FAN_IN2, OUTPUT);
  pinMode(STEP_PIN, OUTPUT); pinMode(DIR_PIN, OUTPUT);
  digitalWrite(FAN_IN1, LOW); digitalWrite(FAN_IN2, LOW);
  digitalWrite(STEP_PIN, LOW); digitalWrite(DIR_PIN, LOW);
  spark.attach(EXTRUDER_PIN, 1000, 2000);
  spark.writeMicroseconds(1500);
  // HPS-2027 remains detached until explicit CENTER or SWEEP.
  Serial.begin(115200);
  lastCommandMs = millis();
  lastServoMs = millis();
  stopAll(true);
}

void loop() {
  runStepper();
  runGuide();
  byte count = 0;
  while (Serial.available() > 0 && count++ < 32) {
    char c = (char)Serial.read();
    if (c == '\n') {
      lineBuffer[linePos] = '\0';
      processLine(lineBuffer);
      linePos = 0;
    } else if (c != '\r') {
      if (linePos < sizeof(lineBuffer) - 1) lineBuffer[linePos++] = c;
      else linePos = 0;
    }
  }
  if (!timedOut && millis() - lastCommandMs > TIMEOUT_MS) {
    stopAll(true);
    timedOut = true;
  }
}
