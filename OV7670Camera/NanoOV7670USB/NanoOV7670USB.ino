// Nano ESP32 + OV7670 (no external FIFO), USB grayscale capture.
// This sketch is independent of RecyclerMega_Latest.ino.
// Board: Arduino Nano ESP32 (arduino:esp32:nano_nora).
#include <Arduino.h>
#include "esp_camera.h"
#include "esp_log.h"
#include "driver/gpio.h"
#include "soc/gpio_struct.h"
#include "soc/gpio_sig_map.h"

// SCCB API from the installed esp32-camera component, reused without opening a second bus.
extern "C" uint8_t SCCB_Read(uint8_t address, uint8_t reg);

#if !defined(CONFIG_IDF_TARGET_ESP32S3)
#error "This pin map is for the Arduino Nano ESP32 / ESP32-S3."
#endif

// RAW ESP32 GPIO numbers, matching the user's Nano header wiring.
// The camera driver takes GPIO numbers, regardless of Arduino pin-number mode.
constexpr int CAM_SDA = 11;    // SIOD -> A4
constexpr int CAM_SCL = 12;    // SIOC -> A5
constexpr int CAM_VSYNC = 5;   // VSYNC -> D2
constexpr int CAM_HREF = 6;    // HREF -> D3
constexpr int CAM_PCLK = 7;    // PCLK -> D4
constexpr int CAM_XCLK = 8;    // XCLK -> D5
constexpr int CAM_D0 = 9;      // camera D0 -> Nano D6
constexpr int CAM_D1 = 10;     // camera D1 -> Nano D7
constexpr int CAM_D2 = 17;     // camera D2 -> Nano D8
constexpr int CAM_D3 = 18;     // camera D3 -> Nano D9
constexpr int CAM_D4 = 21;     // camera D4 -> Nano D10
constexpr int CAM_D5 = 38;     // camera D5 -> Nano D11
constexpr int CAM_D6 = 47;     // camera D6 -> Nano D12
constexpr int CAM_D7 = 48;     // camera D7 -> Nano D13
// RESET and PWDN were not included in the supplied wiring table.
// -1 means not driven by the Nano. The module must hold RESET high / PWDN low.
constexpr int CAM_RESET = -1;
constexpr int CAM_PWDN = -1;
constexpr uint32_t USB_BAUD = 921600;
constexpr uint32_t XCLK_HZ = 10000000;
constexpr uint16_t FRAME_WIDTH = 320;
constexpr uint16_t FRAME_HEIGHT = 240;
constexpr char FIRMWARE_VERSION[] = "nano-ov7670-usb-1.2";

bool cameraAttempted = false;
bool cameraReady = false;
uint16_t sensorPID = 0;
uint8_t sensorVER = 0, sensorMIDH = 0, sensorMIDL = 0;
char cameraError[128] = "";
uint32_t sequenceNumber = 0;
char commandBuffer[64];
size_t commandLength = 0;
bool commandOverflow = false;

struct FrameHeader {
  char magic[4];
  uint32_t sequence;
  uint16_t width;
  uint16_t height;
  uint32_t length;
  uint32_t crc32;
};
static_assert(sizeof(FrameHeader) == 20, "USB frame header layout changed");

void initializeCamera() {
  if (cameraAttempted) return;
  cameraAttempted = true;
  if (!psramFound()) {
    snprintf(cameraError, sizeof(cameraError), "PSRAM unavailable; select the Nano ESP32 board configuration");
    return;
  }

  camera_config_t config = {};
  config.pin_pwdn = CAM_PWDN;
  config.pin_reset = CAM_RESET;
  config.pin_xclk = CAM_XCLK;
  config.pin_sccb_sda = CAM_SDA;
  config.pin_sccb_scl = CAM_SCL;
  config.pin_d0 = CAM_D0;
  config.pin_d1 = CAM_D1;
  config.pin_d2 = CAM_D2;
  config.pin_d3 = CAM_D3;
  config.pin_d4 = CAM_D4;
  config.pin_d5 = CAM_D5;
  config.pin_d6 = CAM_D6;
  config.pin_d7 = CAM_D7;
  config.pin_vsync = CAM_VSYNC;
  config.pin_href = CAM_HREF;
  config.pin_pclk = CAM_PCLK;
  config.xclk_freq_hz = XCLK_HZ;
  config.ledc_timer = LEDC_TIMER_0;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.pixel_format = PIXFORMAT_GRAYSCALE;
  config.frame_size = FRAMESIZE_QVGA;
  config.jpeg_quality = 12;  // unused for grayscale
  config.fb_count = 1;
  config.fb_location = CAMERA_FB_IN_PSRAM;
  config.grab_mode = CAMERA_GRAB_WHEN_EMPTY;

  esp_err_t result = esp_camera_init(&config);
  if (result != ESP_OK) {
    snprintf(cameraError, sizeof(cameraError), "esp_camera_init: %s (0x%X)",
             esp_err_to_name(result), static_cast<unsigned>(result));
    return;
  }
  sensor_t *sensor = esp_camera_sensor_get();
  if (!sensor) {
    snprintf(cameraError, sizeof(cameraError), "No sensor identity available");
    esp_camera_deinit();
    return;
  }
  sensorPID = sensor->id.PID;
  sensorVER = sensor->id.VER;
  sensorMIDH = sensor->id.MIDH;
  sensorMIDL = sensor->id.MIDL;
  if (sensorPID != OV7670_PID) {
    snprintf(cameraError, sizeof(cameraError), "Unexpected sensor PID 0x%X; OV7670 required", sensorPID);
    esp_camera_deinit();
    return;
  }
  cameraReady = true;
}


// On-demand diagnostics never drive pixel-data inputs or alter their pull resistors.
void sendDiagnostics() {
  if (!cameraReady) {
    Serial.println("OV7670_ERROR Camera not ready for diagnostics");
    return;
  }
  const int dataPins[8] = {CAM_D0, CAM_D1, CAM_D2, CAM_D3, CAM_D4, CAM_D5, CAM_D6, CAM_D7};
  uint32_t ones[8] = {}, activeOnes[8] = {};
  uint32_t activeSamples = 0, hrefHigh = 0, vsyncHigh = 0, pclkHigh = 0;
  constexpr uint32_t samples = 40000;
  uint32_t started = micros();
  for (uint32_t sample = 0; sample < samples; ++sample) {
    uint32_t low = GPIO.in;
    uint32_t high = GPIO.in1.val;
    bool active = (low >> CAM_HREF) & 1U;
    if (active) ++activeSamples;
    hrefHigh += active;
    vsyncHigh += (low >> CAM_VSYNC) & 1U;
    pclkHigh += (low >> CAM_PCLK) & 1U;
    for (int bit = 0; bit < 8; ++bit) {
      bool level = dataPins[bit] < 32 ? ((low >> dataPins[bit]) & 1U)
                                    : ((high >> (dataPins[bit] - 32)) & 1U);
      ones[bit] += level;
      if (active) activeOnes[bit] += level;
    }
    if ((sample & 4095U) == 0) yield();
  }
  uint32_t sampleUs = micros() - started;
  Serial.printf("OV7670_DIAG {\"samples\":%lu,\"sample_us\":%lu,\"active_samples\":%lu,"
                "\"href_high\":%lu,\"vsync_high\":%lu,\"pclk_high\":%lu,\"gpio\":[",
                (unsigned long)samples, (unsigned long)sampleUs, (unsigned long)activeSamples,
                (unsigned long)hrefHigh, (unsigned long)vsyncHigh, (unsigned long)pclkHigh);
  for (int bit = 0; bit < 8; ++bit) Serial.printf("%s%d", bit ? "," : "", dataPins[bit]);
  Serial.print("],\"matrix_gpio\":[");
  for (int bit = 0; bit < 8; ++bit)
    Serial.printf("%s%lu", bit ? "," : "", (unsigned long)GPIO.func_in_sel_cfg[CAM_DATA_IN0_IDX + bit].func_sel);
  Serial.print("],\"data_high\":[");
  for (int bit = 0; bit < 8; ++bit) Serial.printf("%s%lu", bit ? "," : "", (unsigned long)ones[bit]);
  Serial.print("],\"active_data_high\":[");
  for (int bit = 0; bit < 8; ++bit) Serial.printf("%s%lu", bit ? "," : "", (unsigned long)activeOnes[bit]);
  Serial.print("],\"registers\":{");
  const uint8_t registers[] = {0x00,0x03,0x04,0x07,0x0A,0x0B,0x10,0x11,0x12,0x13,0x15,0x3A,0x3D,0x40,0x70,0x71};
  sensor_t *sensor = esp_camera_sensor_get();
  for (size_t i = 0; i < sizeof(registers); ++i)
    Serial.printf("%s\"%02X\":%u", i ? "," : "", registers[i], SCCB_Read(sensor->slv_addr, registers[i]));
  Serial.println("}}");
}

void setTestPattern(bool enable) {
  if (!cameraReady) {
    Serial.println("OV7670_ERROR Camera not ready for test pattern");
    return;
  }
  sensor_t *sensor = esp_camera_sensor_get();
  int result = sensor->set_colorbar ? sensor->set_colorbar(sensor, enable ? 1 : 0) : -1;
  Serial.printf("OV7670_DIAG {\"test_pattern\":%s,\"result\":%d,\"scaling_xsc\":%u,\"scaling_ysc\":%u}\n",
                enable ? "true" : "false", result,
                SCCB_Read(sensor->slv_addr, 0x70), SCCB_Read(sensor->slv_addr, 0x71));
}

void sendIdentity() {
  initializeCamera();
  Serial.printf(
    "OV7670_INFO {\"protocol\":1,\"firmware\":\"%s\",\"board\":\"Arduino Nano ESP32\","
    "\"chip\":\"%s\",\"chip_revision\":%u,\"sensor\":\"%s\",\"sensor_pid\":%u,"
    "\"sensor_ver\":%u,\"sensor_midh\":%u,\"sensor_midl\":%u,"
    "\"camera_ready\":%s,\"pixel_format\":\"GRAY8\",\"width\":%u,\"height\":%u,"
    "\"external_fifo\":false,\"error\":\"%s\"}\n",
    FIRMWARE_VERSION, ESP.getChipModel(), ESP.getChipRevision(),
    sensorPID == OV7670_PID ? "OV7670" : "unidentified",
    sensorPID, sensorVER, sensorMIDH, sensorMIDL,
    cameraReady ? "true" : "false", FRAME_WIDTH, FRAME_HEIGHT, cameraError);
}

uint32_t frameCRC32(const uint8_t *data, size_t length) {
  uint32_t crc = 0xFFFFFFFFU;
  for (size_t i = 0; i < length; ++i) {
    crc ^= data[i];
    for (uint8_t bit = 0; bit < 8; ++bit)
      crc = (crc >> 1) ^ (0xEDB88320U & (0U - (crc & 1U)));
    if ((i & 4095U) == 0) yield();
  }
  return ~crc;
}

bool writeAll(const uint8_t *data, size_t length) {
  size_t written = 0;
  unsigned long started = millis();
  while (written < length) {
    // The core's logical Serial flag can remain false after CDC reconnects
    // even while identity replies work. Use actual USB writes and a deadline.
    if (millis() - started > 10000UL) return false;
    size_t chunk = length - written;
    if (chunk > 1024) chunk = 1024;
    written += Serial.write(data + written, chunk);
    yield();
  }
  return true;
}

void sendFrame() {
  if (!cameraReady) {
    Serial.print("OV7670_ERROR Camera not ready: ");
    Serial.println(cameraError);
    return;
  }
  // Discard the queued buffer, then wait for a newly acquired frame.
  // This prevents sending a frame buffered during the preceding USB transfer.
  camera_fb_t *oldFrame = esp_camera_fb_get();
  if (!oldFrame) {
    Serial.println("OV7670_ERROR Capture timed out; check XCLK, PCLK, VSYNC, HREF and data wiring");
    return;
  }
  esp_camera_fb_return(oldFrame);
  camera_fb_t *frame = esp_camera_fb_get();
  if (!frame) {
    Serial.println("OV7670_ERROR Fresh frame capture timed out");
    return;
  }
  if (frame->format != PIXFORMAT_GRAYSCALE ||
      frame->width != FRAME_WIDTH || frame->height != FRAME_HEIGHT ||
      frame->len != static_cast<size_t>(FRAME_WIDTH) * FRAME_HEIGHT) {
    esp_camera_fb_return(frame);
    Serial.println("OV7670_ERROR Unexpected frame format or dimensions");
    return;
  }
  FrameHeader header = {{'O', 'V', 'F', '1'}, ++sequenceNumber,
                        static_cast<uint16_t>(frame->width),
                        static_cast<uint16_t>(frame->height),
                        static_cast<uint32_t>(frame->len),
                        frameCRC32(frame->buf, frame->len)};
  if (writeAll(reinterpret_cast<const uint8_t *>(&header), sizeof(header)))
    writeAll(frame->buf, frame->len);
  esp_camera_fb_return(frame);
}

void setup() {
  Serial.begin(USB_BAUD);
  esp_log_level_set("*", ESP_LOG_NONE);  // Keep diagnostic text out of frame packets.
  // Camera initialization is deferred until the viewer requests its identity.
  // Do not drive LED_BUILTIN: Nano D13 is being used for camera data D7.
}

void loop() {
  while (Serial.available()) {
    char next = static_cast<char>(Serial.read());
    if (next == '\r') continue;
    if (next == '\n') {
      commandBuffer[commandLength] = '\0';
      if (commandOverflow)
        Serial.println("OV7670_ERROR Command too long");
      else if (strcmp(commandBuffer, "OV7670_HELLO") == 0)
        sendIdentity();
      else if (strcmp(commandBuffer, "OV7670_FRAME") == 0)
        sendFrame();
      else if (strcmp(commandBuffer, "OV7670_DIAG") == 0)
        sendDiagnostics();
      else if (strcmp(commandBuffer, "OV7670_TEST_ON") == 0)
        setTestPattern(true);
      else if (strcmp(commandBuffer, "OV7670_TEST_OFF") == 0)
        setTestPattern(false);
      else if (commandLength)
        Serial.println("OV7670_ERROR Unknown command");
      commandLength = 0;
      commandOverflow = false;
    } else if (commandLength < sizeof(commandBuffer) - 1) {
      commandBuffer[commandLength++] = next;
    } else {
      commandOverflow = true;
    }
  }
  delay(1);
}
