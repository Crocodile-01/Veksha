# VEKSHA - IoT Emergency Health & Rescue Alert System

An educational IoT-based health monitoring and rescue alert system powered by the ESP32 microcontroller, MAX30102 sensor, FastAPI backend, and a modern web dashboard.

> **Disclaimer**: This is a prototype system intended strictly for academic and demonstration purposes. It does not provide medical-grade accuracy or diagnostic capabilities.

---

## Project Roadmap

| Phase | Milestone | Description | Status |
| :--- | :--- | :--- | :--- |
| **Phase 1** | **Backend Service & Sensor Test** | FastAPI backend (`/health`, `/readings`) & ESP32 test sketch | **Complete** |
| **Phase 2** | **VEKSHA Web Dashboard** | Modern, responsive real-time UI with live vitals, status & trends | **Complete** |
| **Phase 3** | **ESP32 Wi-Fi Telemetry Firmware** | ESP32 reading MAX30102 & HTTP POSTing to `/api/v1/readings` | **Complete (Ready to flash)** |
| **Phase 4** | **Alerts & SOS Emergency Workflow** | Manual SOS trigger, threshold alerts, and rescue status cards | Queued |
| **Phase 5** | **GPS Module Integration** | Integrate NEO-6M GPS for live rescue coordinates | Postponed |

---

## File Structure

```text
veksha/
├── README.md                                    # Project documentation & roadmap
├── dashboard/
│   ├── index.html                               # Responsive medical-IoT dashboard UI
│   ├── style.css                                # Dark glassmorphic design system
│   └── app.js                                   # Telemetry polling, states & canvas charting
├── firmware/
│   ├── esp32_max30102_test/
│   │   └── esp32_max30102_test.ino              # Phase 1 standalone sensor test
│   └── esp32_veksha_telemetry/
│       └── esp32_veksha_telemetry.ino           # Phase 3 full Wi-Fi telemetry firmware
└── server/
    ├── main.py                                  # FastAPI app (serves API & mounts dashboard)
    ├── test_main.py                             # Pytest test suite
    ├── requirements.txt                         # Dependencies (fastapi, uvicorn, pydantic, pytest)
    └── README.md                                # Backend documentation
```

---

## Hardware Pin Connections (ESP32 DevKit V1 to MAX30102)

| MAX30102 Pin | ESP32 DevKit V1 Pin | Notes |
| :--- | :--- | :--- |
| **VIN / VCC** | **3V3** | Connect to 3.3V |
| **GND** | **GND** | Ground reference |
| **SDA** | **GPIO 21** | I2C Data |
| **SCL** | **GPIO 22** | I2C Clock |
| **INT** | *Not connected* | Polling / buffer acquisition |
| *NEO-6M GPS* | *Disconnected* | Postponed until wiring & power verified |
