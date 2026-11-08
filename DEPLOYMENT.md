# VEKSHA - Remote Access & Production Deployment Guide

This guide describes how to deploy the **VEKSHA Backend & Dashboard** to cloud hosting platforms (such as Render, Railway, Fly.io, or any VPS) so that:
1. The **ESP32 IoT node** can stream vitals over Wi-Fi from anywhere.
2. **Family members and clinicians** can access the real-time dashboard securely via an HTTPS domain.
3. **Firebase Firestore** remains the cloud database with zero credential exposure.

---

## Architecture Overview

```
 [ESP32 + MAX30102] ──(HTTP POST Telemetry)──┐
                                             │
 [Web Browser Dashboard] ──(HTTPS Poll)──────┼──► [FastAPI Cloud Service]
                                             │         │
                                             │         ├─► [Firebase Firestore]
                                             │         └─► [MSG91 SMS Gateway]
```

The FastAPI service serves both the API endpoints (`/api/v1/*`) and mounts the static web dashboard (`/`).

---

## 1. Cloud Credential Configuration (Zero File Uploads)

Cloud hosts like Render, Railway, or Fly.io use environment variables rather than secret files.

To configure Firebase on any cloud host without uploading the JSON file:
1. Open your downloaded service account file:
   `secrets/veksha-health-monitor-firebase-adminsdk-fbsvc-2df14b18ff.json`
2. Minify or copy its full JSON contents:
   ```json
   {"type":"service_account","project_id":"veksha-health-monitor",...}
   ```
3. In your cloud provider's dashboard, set the environment variable:
   * **Key:** `FIREBASE_SERVICE_ACCOUNT_JSON`
   * **Value:** *(paste the raw JSON string)*

`firebase_service.py` will automatically detect `FIREBASE_SERVICE_ACCOUNT_JSON` and authenticate the Admin SDK.

---

## 2. Deploying to Render.com (Recommended Free/Low-Cost Option)

1. Create a free account at [render.com](https://render.com).
2. Click **New +** → **Web Service** → Connect your GitHub repository.
3. Configure the service:
   * **Name:** `veksha-backend`
   * **Environment:** `Python 3`
   * **Root Directory:** *(leave blank or set to project root)*
   * **Build Command:**
     ```bash
     pip install -r server/requirements.txt
     ```
   * **Start Command:**
     ```bash
     cd server && uvicorn main:app --host 0.0.0.0 --port $PORT --workers 2
     ```
4. In the **Environment Variables** tab, add:
   * `FIREBASE_PROJECT_ID`: `veksha-health-monitor`
   * `FIREBASE_SERVICE_ACCOUNT_JSON`: *(your JSON contents)*
   * `FIREBASE_ENABLED`: `true`
   * `CORS_ORIGINS`: `*` *(or your custom domain)*
   * `ALERT_HR_MIN`: `50.0`
   * `ALERT_HR_MAX`: `120.0`
   * `ALERT_SPO2_MIN`: `90.0`
   * `SMS_ENABLED`: `false` *(set true after MSG91 setup)*
5. Click **Create Web Service**.
6. Render will assign a free HTTPS URL: `https://veksha-backend.onrender.com`.

---

## 3. Deploying to Railway.app

1. Sign in to [railway.app](https://railway.app).
2. Click **New Project** → **Deploy from GitHub repo**.
3. Railway automatically detects `docker-compose.yml` or `server/Dockerfile`.
4. In **Variables**, enter:
   * `PORT`: `8000`
   * `FIREBASE_PROJECT_ID`: `veksha-health-monitor`
   * `FIREBASE_SERVICE_ACCOUNT_JSON`: *(your JSON contents)*
   * `FIREBASE_ENABLED`: `true`
   * `CORS_ORIGINS`: `*`
5. Under **Settings** → **Networking**, click **Generate Domain** (e.g. `veksha-backend.up.railway.app`).

---

## 4. Self-Hosted VPS / Docker Deployment

If deploying to an Ubuntu/Debian VPS (DigitalOcean, AWS EC2, Linode):

```bash
# 1. Clone repository on the VPS
git clone https://github.com/your-username/veksha.git /opt/veksha
cd /opt/veksha

# 2. Place credentials in secrets/
cp path/to/your-key.json secrets/veksha-health-monitor-firebase-adminsdk-fbsvc-2df14b18ff.json

# 3. Configure production .env
cp server/.env.example server/.env
nano server/.env

# 4. Build and run using Docker Compose
docker compose up -d --build

# 5. Verify status
docker compose ps
curl http://localhost:8000/health
```

---

## 5. Updating the ESP32 Firmware for Remote Deployed Backend

Once your backend is live with an HTTPS domain (e.g., `https://veksha-backend.onrender.com`):

1. Open `firmware/esp32_veksha_telemetry/esp32_veksha_telemetry.ino`.
2. Update line 41:
   ```cpp
   const char* SERVER_URL = "https://veksha-backend.onrender.com/api/v1/readings";
   ```
3. Upload the firmware to the ESP32.
4. Your ESP32 can now transmit vitals from any 2.4 GHz Wi-Fi network without requiring your laptop to be on the same local network!

---

## 6. Production Security Checklist

- [x] Service account private key is loaded via env var or gitignored `secrets/` directory.
- [x] Firestore security rules block unauthorized public browser reads/writes (Admin SDK bypasses client rules).
- [x] CORS origins can be restricted in `.env` (`CORS_ORIGINS=https://your-domain.com`).
- [x] Input schema is validated with strict Pydantic range checks.
- [x] SOS and SMS endpoints enforce rate limiting & cooldowns.

