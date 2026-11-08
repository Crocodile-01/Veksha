# VEKSHA Backend Service (Phase 1)

FastAPI telemetry ingestion service for the VEKSHA IoT Health & Rescue Alert System.

---

## 1. Prerequisites
- Python 3.10+ installed
- Windows PowerShell

---

## 2. Setup Instructions (Windows PowerShell)

Open PowerShell and navigate to the `server/` directory:

```powershell
cd c:\Users\JAYESH\OneDrive\Desktop\veksha\server
```

### Step A: Create and Activate Python Virtual Environment
```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```
*(If PowerShell shows an execution policy error, run `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope Process` first).*

### Step B: Install Dependencies
```powershell
pip install -r requirements.txt
```

---

## 3. Running Unit Tests

Run pytest to verify endpoint logic and data validation:

```powershell
pytest -v
```

All tests should pass:
- Health check endpoints
- Valid reading acceptance
- No-finger reading acceptance
- Missing vitals rejection
- Out-of-bounds vitals rejection
- Latest reading retrieval

---

## 4. Starting the Server

Start the FastAPI server on port 8000:

```powershell
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

- **Interactive API Documentation (Swagger)**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **Health Check**: [http://localhost:8000/api/v1/health](http://localhost:8000/api/v1/health)

---

## 5. Testing Endpoints from PowerShell

### Test 1: Health Check (GET)
```powershell
Invoke-RestMethod -Uri "http://localhost:8000/api/v1/health" -Method Get
```

**Expected Response**:
```json
{
  "status": "online",
  "service": "VEKSHA Emergency Health Monitoring Backend",
  "version": "1.0.0",
  "server_time": "2026-10-29T..."
}
```

### Test 2: Ingest Valid Sensor Reading (POST)
```powershell
$body = @{
    heart_rate = 76.5
    spo2 = 98.0
    sensor_status = "VALID_DATA"
    device_id = "esp32-node-01"
} | ConvertTo-Json

Invoke-RestMethod -Uri "http://localhost:8000/api/v1/readings" -Method Post -Body $body -ContentType "application/json"
```

### Test 3: Ingest "No Finger" Event (POST)
```powershell
$body = @{
    heart_rate = 0
    spo2 = 0
    sensor_status = "NO_FINGER"
    device_id = "esp32-node-01"
} | ConvertTo-Json

Invoke-RestMethod -Uri "http://localhost:8000/api/v1/readings" -Method Post -Body $body -ContentType "application/json"
```

### Test 4: Retrieve Latest Reading (GET)
```powershell
Invoke-RestMethod -Uri "http://localhost:8000/api/v1/readings/latest" -Method Get
```
