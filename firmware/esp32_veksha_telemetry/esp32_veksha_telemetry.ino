/*
 * VEKSHA - IoT Emergency Health & Rescue Alert System
 * Firmware: ESP32 + MAX30102 Wi-Fi Telemetry Node
 *
 * Hardware:
 *   - ESP32 DevKit V1
 *   - MAX30102 Pulse Oximeter Sensor
 *   (NEO-6M GPS module is postponed and remains disconnected)
 *
 * Pin Connections (ESP32 to MAX30102 via I2C):
 *   MAX30102 VIN  -> ESP32 3V3
 *   MAX30102 GND  -> ESP32 GND
 *   MAX30102 SDA  -> ESP32 GPIO 21
 *   MAX30102 SCL  -> ESP32 GPIO 22
 *   MAX30102 INT  -> Not connected
 *
 * Signal Processing Improvements (Task 1):
 *   - Finger stability & optical saturation bounds (50,000 <= IR <= 245,000)
 *   - Stabilization warmup (CALIBRATING state for first 3 cycles upon placement)
 *   - Signal Quality Indicator (SQI) using optical AC peak-to-peak range
 *   - 5-point Running Median Filter to eliminate harmonic spikes (e.g. 80 -> 180 BPM)
 *   - Physiological rate-of-change limiter (outlier rejection for abrupt jumps)
 *   - FIFO clearing post-HTTP transmit to prevent timing desynchronisation
 *   - Immediate filter buffer purge on finger removal to prevent stale readings
 *
 * Disclaimer:
 *   Educational student prototype. Not intended for clinical or medical diagnosis.
 */

#include <Wire.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include "MAX30105.h"
#include "spo2_algorithm.h"

// ============================================================================
// 1. NETWORK & BACKEND CONFIGURATION
// ============================================================================
const char* WIFI_SSID     = "****";         // Replace with your 2.4GHz Wi-Fi name
const char* WIFI_PASSWORD = "234567890";     // Replace with your Wi-Fi password

// Computer's local LAN IP address or deployed backend URL
// Example: "http://192.168.1.11:8000/api/v1/readings"
const char* SERVER_URL    = "http://192.168.1.11:8000/api/v1/readings";

const char* DEVICE_ID     = "esp32-veksha-01";

// ============================================================================
// 2. HARDWARE & SENSOR DEFINITIONS
// ============================================================================
MAX30105 particleSensor;

// Finger detection optical thresholds
const uint32_t FINGER_MIN_THRESHOLD = 50000;   // Minimum IR to confirm tissue contact
const uint32_t FINGER_MAX_THRESHOLD = 245000;  // Maximum IR before ADC saturation

// Buffer length for Maxim algorithm: 100 samples (4s at 25 Hz)
#define BUFFER_SIZE 100
uint32_t irBuffer[BUFFER_SIZE];
uint32_t redBuffer[BUFFER_SIZE];

int32_t rawSpo2 = 0;
int8_t validSPO2 = 0;
int32_t rawHeartRate = 0;
int8_t validHeartRate = 0;

bool sensorReady = false;
bool fingerPresent = false;
int stabilizationCycles = 0;
const int REQUIRED_STABILIZATION_CYCLES = 3; // ~3 seconds of stable contact required

// ----------------------------------------------------------------------------
// Median Filter & Outlier Limiter State (Filter Window = 5)
// ----------------------------------------------------------------------------
#define FILTER_SIZE 5
int32_t hrFilterBuf[FILTER_SIZE];
int32_t spo2FilterBuf[FILTER_SIZE];
uint8_t filterSampleCount = 0;
uint8_t filterWriteIdx = 0;

int32_t lastConfirmedHR = 0;
int32_t lastConfirmedSpO2 = 0;
uint8_t pendingOutlierCount = 0; // Number of consecutive frames an outlier persisted

unsigned long lastTransmissionTime = 0;
const unsigned long NO_FINGER_TX_INTERVAL_MS = 3000; // Throttle "no finger" frames

// ============================================================================
// 3. SIGNAL PROCESSING & FILTERING UTILITIES
// ============================================================================

/**
 * Reset all filter memory and calibration counters.
 * Called immediately when a finger is removed to prevent stale data reuse.
 */
void resetSignalFilters() {
  filterSampleCount = 0;
  filterWriteIdx = 0;
  lastConfirmedHR = 0;
  lastConfirmedSpO2 = 0;
  pendingOutlierCount = 0;
  stabilizationCycles = 0;
  for (int i = 0; i < FILTER_SIZE; i++) {
    hrFilterBuf[i] = 0;
    spo2FilterBuf[i] = 0;
  }
}

/**
 * Compute the median value of an integer array of length n.
 */
int32_t computeMedian(const int32_t* arr, uint8_t n) {
  if (n == 0) return 0;
  if (n == 1) return arr[0];

  int32_t sorted[FILTER_SIZE];
  for (uint8_t i = 0; i < n; i++) sorted[i] = arr[i];

  // Insertion sort (efficient for small N <= 5)
  for (uint8_t i = 1; i < n; i++) {
    int32_t key = sorted[i];
    int8_t j = i - 1;
    while (j >= 0 && sorted[j] > key) {
      sorted[j + 1] = sorted[j];
      j--;
    }
    sorted[j + 1] = key;
  }

  return sorted[n / 2];
}

/**
 * Signal Quality Indicator (SQI) check on optical buffer.
 * Evaluates AC peak-to-peak amplitude on IR channel.
 * Returns true if signal shows pulsatile AC component without gross motion clipping.
 */
bool checkSignalQuality(const uint32_t* ir, uint16_t len) {
  uint32_t minIR = 0xFFFFFFFF;
  uint32_t maxIR = 0;

  for (uint16_t i = 0; i < len; i++) {
    if (ir[i] < minIR) minIR = ir[i];
    if (ir[i] > maxIR) maxIR = ir[i];
  }

  uint32_t p2p = maxIR - minIR;

  // Signal must have pulsatile AC variation (> 200 counts) but not extreme motion clipping (> 55000)
  if (p2p < 200 || p2p > 55000) {
    Serial.print(F("[SQI] Poor signal quality: IR Peak-to-Peak = "));
    Serial.print(p2p);
    Serial.println(F(" (outside 200 - 55000 acceptable range)."));
    return false;
  }
  return true;
}

/**
 * Push new raw reading into rolling median filter with outlier rejection.
 * Returns true if a reliable, filtered reading is ready.
 */
bool pushFilteredVitals(int32_t rawHR, int32_t rawO2, int32_t* outHR, int32_t* outO2) {
  // Physiological validity bounds
  if (rawHR < 40 || rawHR > 220 || rawO2 < 70 || rawO2 > 100) {
    return false;
  }

  // Outlier Check against previous confirmed reading
  if (filterSampleCount >= 3 && lastConfirmedHR > 0) {
    int32_t deltaHR = abs(rawHR - lastConfirmedHR);
    // If HR abruptly changes by > 28 BPM in ~1 sec, it is likely a harmonic double-peak
    if (deltaHR > 28) {
      pendingOutlierCount++;
      if (pendingOutlierCount < 2) {
        Serial.print(F("[FILTER] Rejected harmonic/outlier spike: "));
        Serial.print(rawHR);
        Serial.print(F(" BPM (jumped "));
        Serial.print(deltaHR);
        Serial.println(F(" BPM from baseline). Awaiting confirmation."));
        return false;
      }
      // If the shift persisted for 2 consecutive cycles, accept it as genuine physiological change
    } else {
      pendingOutlierCount = 0;
    }
  }

  // Store in circular buffer
  hrFilterBuf[filterWriteIdx] = rawHR;
  spo2FilterBuf[filterWriteIdx] = rawO2;
  filterWriteIdx = (filterWriteIdx + 1) % FILTER_SIZE;
  if (filterSampleCount < FILTER_SIZE) filterSampleCount++;

  // Need at least 3 points for a reliable median
  if (filterSampleCount < 3) {
    return false;
  }

  *outHR = computeMedian(hrFilterBuf, filterSampleCount);
  *outO2 = computeMedian(spo2FilterBuf, filterSampleCount);

  lastConfirmedHR = *outHR;
  lastConfirmedSpO2 = *outO2;
  return true;
}

// ============================================================================
// 4. WI-FI & HTTP TELEMETRY TRANSMISSION
// ============================================================================
void connectWiFi() {
  Serial.println();
  Serial.print(F("[WiFi] Connecting to SSID: "));
  Serial.println(WIFI_SSID);

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 20) {
    delay(500);
    Serial.print(F("."));
    attempts++;
  }

  if (WiFi.status() == WL_CONNECTED) {
    Serial.println();
    Serial.print(F("[WiFi] Connected! ESP32 IP: "));
    Serial.println(WiFi.localIP());
  } else {
    Serial.println();
    Serial.println(F("[WiFi] Connection timeout. Will retry in background..."));
  }
}

void ensureWiFiConnected() {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println(F("[WiFi] Connection lost. Reconnecting..."));
    WiFi.disconnect();
    WiFi.reconnect();
    unsigned long start = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - start < 5000) {
      delay(250);
      Serial.print(F("."));
    }
    if (WiFi.status() == WL_CONNECTED) {
      Serial.println(F("\n[WiFi] Reconnected!"));
    } else {
      Serial.println(F("\n[WiFi] Reconnect failed."));
    }
  }
}

bool sendTelemetry(const char* statusStr, float hr, float spo2) {
  ensureWiFiConnected();

  if (WiFi.status() != WL_CONNECTED) {
    Serial.println(F("[HTTP] Transmission skipped: WiFi offline."));
    return false;
  }

  HTTPClient http;
  http.begin(SERVER_URL);
  http.addHeader("Content-Type", "application/json");
  http.setTimeout(3500);

  char jsonPayload[256];
  if (strcmp(statusStr, "VALID_DATA") == 0) {
    snprintf(jsonPayload, sizeof(jsonPayload),
      "{\"heart_rate\":%.1f,\"spo2\":%.1f,\"sensor_status\":\"%s\",\"device_id\":\"%s\"}",
      hr, spo2, statusStr, DEVICE_ID);
  } else {
    // For NO_FINGER, INVALID_DATA, or CALIBRATING, report 0 vitals
    snprintf(jsonPayload, sizeof(jsonPayload),
      "{\"heart_rate\":0,\"spo2\":0,\"sensor_status\":\"%s\",\"device_id\":\"%s\"}",
      statusStr, DEVICE_ID);
  }

  Serial.print(F("[HTTP POST] Payload: "));
  Serial.println(jsonPayload);

  int httpCode = http.POST((uint8_t*)jsonPayload, strlen(jsonPayload));

  if (httpCode > 0) {
    Serial.print(F("[HTTP] Status: "));
    Serial.println(httpCode);
    if (httpCode == HTTP_CODE_CREATED || httpCode == HTTP_CODE_OK) {
      http.end();
      return true;
    }
  } else {
    Serial.print(F("[HTTP] POST failed: "));
    Serial.println(http.errorToString(httpCode).c_str());
  }

  http.end();
  return false;
}

// ============================================================================
// 5. SETUP
// ============================================================================
void setup() {
  Serial.begin(115200);
  delay(1000);

  Serial.println(F("\n=================================================="));
  Serial.println(F("         VEKSHA - Improved Telemetry Node         "));
  Serial.println(F("       ESP32 DevKit V1 + MAX30102 -> Wi-Fi        "));
  Serial.println(F("=================================================="));

  // Initialize I2C (GPIO 21 = SDA, GPIO 22 = SCL)
  Wire.begin(21, 22);

  // Initialize MAX30102
  Serial.print(F("[Sensor] Initializing MAX30102... "));
  if (!particleSensor.begin(Wire, I2C_SPEED_FAST)) {
    Serial.println(F("FAILED!"));
    Serial.println(F("[ERROR] MAX30102 not detected. Check I2C wiring (SDA=21, SCL=22)."));
    sensorReady = false;
  } else {
    Serial.println(F("SUCCESS!"));
    sensorReady = true;

    // Pulse Oximeter Configuration:
    // LED Brightness: 0x24 (approx 7.2mA - balanced SNR and zero saturation)
    // Sample Average: 4
    // LED Mode: 2 (Red + IR)
    // Sample Rate: 100 Hz (100 / 4 = 25 effective Hz, matches Maxim algorithm FreqS = 25)
    // Pulse Width: 411 us (ADC 18-bit resolution)
    // ADC Range: 4096
    byte ledBrightness = 60;
    byte sampleAverage = 4;
    byte ledMode = 2;
    byte sampleRate = 100;
    int pulseWidth = 411;
    int adcRange = 4096;

    particleSensor.setup(ledBrightness, sampleAverage, ledMode, sampleRate, pulseWidth, adcRange);
    particleSensor.setPulseAmplitudeRed(0x28); // ~8mA drive for Red LED
    particleSensor.setPulseAmplitudeIR(0x28);  // ~8mA drive for IR LED
    particleSensor.setPulseAmplitudeGreen(0);  // Green off

    Serial.println(F("[Sensor] MAX30102 configured with optimal SpO2 SNR parameters."));
  }

  // Connect Wi-Fi
  connectWiFi();

  resetSignalFilters();
  Serial.println(F("\n[Ready] System initialized. Place finger steadily on sensor.\n"));
}

// ============================================================================
// 6. MAIN LOOP
// ============================================================================
void loop() {
  if (!sensorReady) {
    delay(3000);
    Serial.println(F("[Sensor] Retrying sensor connection..."));
    if (particleSensor.begin(Wire, I2C_SPEED_FAST)) {
      sensorReady = true;
      particleSensor.setup();
      Serial.println(F("[Sensor] Reconnected successfully!"));
    }
    return;
  }

  // 1. Initial Quick Check for Finger Presence & Optical Saturation
  uint32_t irCheck = particleSensor.getIR();

  if (irCheck < FINGER_MIN_THRESHOLD) {
    if (fingerPresent) {
      Serial.println(F("[STATUS] Finger removed. Purging signal filters."));
      fingerPresent = false;
      resetSignalFilters();
    }

    // Periodically inform backend that sensor is idle
    if (millis() - lastTransmissionTime >= NO_FINGER_TX_INTERVAL_MS) {
      lastTransmissionTime = millis();
      sendTelemetry("NO_FINGER", 0, 0);
    }

    delay(200);
    return;
  }

  if (irCheck > FINGER_MAX_THRESHOLD) {
    Serial.println(F("[WARNING] Optical saturation detected (excessive ambient light or high pressure)."));
    sendTelemetry("INVALID_DATA", 0, 0);
    delay(500);
    return;
  }

  // Finger is present
  if (!fingerPresent) {
    Serial.println(F("[STATUS] Finger detected. Acquiring optical buffer & baseline..."));
    fingerPresent = true;
    resetSignalFilters();
  }

  // 2. Read first 100 samples (Buffer initialization)
  Serial.println(F("[Algorithm] Sampling 100 IR/Red optical data points..."));
  for (byte i = 0; i < BUFFER_SIZE; i++) {
    while (particleSensor.available() == false) {
      particleSensor.check();
    }

    redBuffer[i] = particleSensor.getRed();
    irBuffer[i]  = particleSensor.getIR();
    particleSensor.nextSample();

    // Check if user lifted finger during sampling
    if (irBuffer[i] < FINGER_MIN_THRESHOLD) {
      Serial.println(F("[Algorithm] Finger lifted during sampling. Resetting."));
      fingerPresent = false;
      resetSignalFilters();
      sendTelemetry("NO_FINGER", 0, 0);
      return;
    }
  }

  // 3. Compute initial Heart Rate and SpO2 using Maxim Algorithm
  maxim_heart_rate_and_oxygen_saturation(
    irBuffer,
    BUFFER_SIZE,
    redBuffer,
    &rawSpo2,
    &validSPO2,
    &rawHeartRate,
    &validHeartRate
  );

  // 4. Continuously Shift 25 Samples and Re-compute with Filtering
  while (true) {
    // Check if finger was removed
    uint32_t currentIR = particleSensor.getIR();
    if (currentIR < FINGER_MIN_THRESHOLD) {
      Serial.println(F("[STATUS] Finger removed during rolling calculation."));
      fingerPresent = false;
      resetSignalFilters();
      sendTelemetry("NO_FINGER", 0, 0);
      break;
    }

    if (currentIR > FINGER_MAX_THRESHOLD) {
      Serial.println(F("[STATUS] Sensor optical saturation during calculation."));
      sendTelemetry("INVALID_DATA", 0, 0);
      delay(300);
      break;
    }

    // Check optical Signal Quality Indicator
    bool sqiGood = checkSignalQuality(irBuffer, BUFFER_SIZE);

    // Warmup stabilization: require 3 cycles (~3 sec) after finger placement
    if (stabilizationCycles < REQUIRED_STABILIZATION_CYCLES) {
      stabilizationCycles++;
      Serial.print(F("[WARMUP] Stabilizing optical contact (cycle "));
      Serial.print(stabilizationCycles);
      Serial.print(F("/"));
      Serial.print(REQUIRED_STABILIZATION_CYCLES);
      Serial.println(F(")..."));

      sendTelemetry("CALIBRATING", 0, 0);
      lastTransmissionTime = millis();
    }
    // Process when algorithm flags valid results and SQI passes
    else if (validHeartRate == 1 && validSPO2 == 1 && sqiGood) {
      int32_t filteredHR = 0;
      int32_t filteredSpO2 = 0;

      bool filterReady = pushFilteredVitals(rawHeartRate, rawSpo2, &filteredHR, &filteredSpO2);

      if (filterReady) {
        Serial.println(F("--------------------------------------------------"));
        Serial.print(F("[VALID VITALS] Raw HR: "));
        Serial.print(rawHeartRate);
        Serial.print(F(" -> Filtered HR: "));
        Serial.print(filteredHR);
        Serial.print(F(" BPM | Raw SpO2: "));
        Serial.print(rawSpo2);
        Serial.print(F(" -> Filtered SpO2: "));
        Serial.print(filteredSpO2);
        Serial.println(F(" %"));
        Serial.println(F("--------------------------------------------------"));

        sendTelemetry("VALID_DATA", (float)filteredHR, (float)filteredSpO2);
        lastTransmissionTime = millis();
      } else {
        Serial.println(F("[FILTER] Accumulating median window or dampening artifact..."));
      }
    } else {
      Serial.print(F("[INVALID VITALS] HR Valid: "));
      Serial.print(validHeartRate);
      Serial.print(F(" ("));
      Serial.print(rawHeartRate);
      Serial.print(F(" BPM) | SpO2 Valid: "));
      Serial.print(validSPO2);
      Serial.print(F(" ("));
      Serial.print(rawSpo2);
      Serial.println(F(" %) | SQI: Poor -> Signal noisy/holding steady..."));

      sendTelemetry("INVALID_DATA", 0, 0);
      lastTransmissionTime = millis();
    }

    // Flush any stale FIFO samples accumulated during HTTP transmission delay
    particleSensor.clearFIFO();

    // Shift previous 75 samples forward by 25 positions
    for (byte i = 25; i < BUFFER_SIZE; i++) {
      redBuffer[i - 25] = redBuffer[i];
      irBuffer[i - 25]  = irBuffer[i];
    }

    // Collect 25 new fresh samples to fill the end of the buffer
    for (byte i = 75; i < BUFFER_SIZE; i++) {
      while (particleSensor.available() == false) {
        particleSensor.check();
      }

      redBuffer[i] = particleSensor.getRed();
      irBuffer[i]  = particleSensor.getIR();
      particleSensor.nextSample();

      // Early break if finger lifted
      if (irBuffer[i] < FINGER_MIN_THRESHOLD) {
        fingerPresent = false;
        break;
      }
    }

    if (!fingerPresent) {
      resetSignalFilters();
      sendTelemetry("NO_FINGER", 0, 0);
      break;
    }

    // Re-calculate Maxim algorithm with refreshed 25 samples
    maxim_heart_rate_and_oxygen_saturation(
      irBuffer,
      BUFFER_SIZE,
      redBuffer,
      &rawSpo2,
      &validSPO2,
      &rawHeartRate,
      &validHeartRate
    );
  }
}
