/*
 * VEKSHA - IoT Emergency Health & Rescue Alert System
 * Phase 1: ESP32 DevKit V1 + MAX30102 Sensor Verification
 *
 * Target Board: DOIT ESP32 DEVKIT V1 (or equivalent ESP32 board)
 * Required Library in Arduino IDE:
 *   - "SparkFun MAX3010x Pulse and Proximity Sensor Library" by SparkFun
 *
 * Hardware Pin Connections (ESP32 DevKit V1 to MAX30102):
 *   MAX30102 VIN  -> ESP32 3V3 (or 5V if board has a dedicated 3.3V regulator)
 *   MAX30102 GND  -> ESP32 GND
 *   MAX30102 SDA  -> ESP32 GPIO 21
 *   MAX30102 SCL  -> ESP32 GPIO 22
 *   MAX30102 INT  -> Not connected (polling mode)
 *
 * Disclaimer:
 *   This is an educational student prototype. Sensor readings are for
 *   informational/demonstration purposes only and not for medical diagnosis.
 */

#include <Wire.h>
#include "MAX30105.h"
#include "heartRate.h"

MAX30105 particleSensor;

// Finger detection threshold for Infrared (IR)
// Below this threshold, no finger is considered detected.
const long FINGER_DETECT_THRESHOLD = 50000;

// Variables for Heart Rate calculation
const byte RATE_SIZE = 4; // Increase for more averaging: 4 is responsive
byte rates[RATE_SIZE];    // Array of heart rates
byte rateSpot = 0;
long lastBeat = 0;        // Time at which the last beat occurred

float beatsPerMinute = 0;
int beatAvg = 0;
bool fingerDetected = false;
bool sensorReady = false;

void setup() {
  Serial.begin(115200);
  delay(1000);
  Serial.println(F("\n=========================================="));
  Serial.println(F("         VEKSHA - Hardware Test 1         "));
  Serial.println(F("    ESP32 + MAX30102 Sensor Diagnostic    "));
  Serial.println(F("=========================================="));

  // Initialize I2C bus on ESP32 default pins: SDA (21), SCL (22)
  Wire.begin(21, 22);

  // Initialize sensor
  Serial.print(F("[INFO] Initializing MAX30102 sensor... "));
  if (!particleSensor.begin(Wire, I2C_SPEED_FAST)) {
    Serial.println(F("FAILED!"));
    Serial.println(F("[ERROR] MAX30102 was not detected on I2C (0x57)."));
    Serial.println(F("  Check:"));
    Serial.println(F("  1. SDA -> GPIO 21, SCL -> GPIO 22"));
    Serial.println(F("  2. Power connections (3.3V & GND)"));
    Serial.println(F("  3. Solder joints / breadboard contacts"));
    sensorReady = false;
    return;
  }

  sensorReady = true;
  Serial.println(F("SUCCESS!"));

  // Configure sensor with standard pulse oximetry settings:
  // - LED Brightness: 0x1F (approx 6.4mA)
  // - Sample Average: 4
  // - LED Mode: 2 (Red + IR)
  // - Sample Rate: 400 Hz
  // - Pulse Width: 411 us
  // - ADC Range: 4096
  byte ledBrightness = 60; // 0=Off to 255=50mA (60 is ~12mA)
  byte sampleAverage = 4;  // Options: 1, 2, 4, 8, 16, 32
  byte ledMode = 2;        // 2 = Red + IR
  byte sampleRate = 100;   // Options: 50, 100, 200, 400, 800, 1000, 1600, 3200
  int pulseWidth = 411;    // Options: 69, 118, 215, 411
  int adcRange = 4096;     // Options: 2048, 4096, 8192, 16384

  particleSensor.setup(ledBrightness, sampleAverage, ledMode, sampleRate, pulseWidth, adcRange);
  particleSensor.setPulseAmplitudeRed(0x0A); // Low Red LED for visual indicator
  particleSensor.setPulseAmplitudeGreen(0);  // Turn off Green LED

  Serial.println(F("[INFO] Sensor configured. Place finger gently on the sensor to begin."));
  Serial.println(F("----------------------------------------------------------------"));
}

void loop() {
  if (!sensorReady) {
    // Retry initialization every 3 seconds if disconnected
    delay(3000);
    Serial.println(F("[RETRY] Retrying sensor connection..."));
    if (particleSensor.begin(Wire, I2C_SPEED_FAST)) {
      sensorReady = true;
      particleSensor.setup();
      Serial.println(F("[RETRY] Sensor reconnected successfully!"));
    }
    return;
  }

  long irValue = particleSensor.getIR();
  long redValue = particleSensor.getRed();

  // Check if a finger is touching the sensor
  if (irValue < FINGER_DETECT_THRESHOLD) {
    if (fingerDetected) {
      Serial.println(F("[STATUS] Finger removed. Readings paused."));
      fingerDetected = false;
      beatsPerMinute = 0;
      beatAvg = 0;
    }
    delay(200);
    return;
  }

  if (!fingerDetected) {
    Serial.println(F("[STATUS] Finger detected. Stabilizing signal..."));
    fingerDetected = true;
  }

  // Detect heartbeat peak using SparkFun's checkForBeat algorithm
  if (checkForBeat(irValue) == true) {
    long delta = millis() - lastBeat;
    lastBeat = millis();

    beatsPerMinute = 60 / (delta / 1000.0);

    // Filter physiologically realistic BPM range (40 - 200 BPM)
    if (beatsPerMinute >= 40 && beatsPerMinute <= 200) {
      rates[rateSpot++] = (byte)beatsPerMinute;
      rateSpot %= RATE_SIZE;

      // Calculate rolling average
      beatAvg = 0;
      for (byte x = 0; x < RATE_SIZE; x++) {
        beatAvg += rates[x];
      }
      beatAvg /= RATE_SIZE;

      Serial.print(F("[BEAT] Instant BPM: "));
      Serial.print(beatsPerMinute, 1);
      Serial.print(F(" | Avg BPM: "));
      Serial.print(beatAvg);
      Serial.print(F(" | IR: "));
      Serial.print(irValue);
      Serial.print(F(" | Red: "));
      Serial.println(redValue);
    }
  }
}
