# VEKSHA SMS Gateway — Android App Setup & Deployment Guide

This Android app turns an Android smartphone (such as OnePlus Nord CE 2 Lite) with an active SIM card into an automated emergency SMS gateway for VEKSHA.

---

## Architecture Flow

```
ESP32 Telemetry / Dashboard SOS
               │
               ▼
      FastAPI Backend
               │
               ▼
Firebase Cloud Messaging (FCM)
               │
               ▼
Android Phone (VEKSHA SMS Gateway App)
               │
               ▼ (SmsManager via SIM)
Family Emergency Contacts (SMS Received)
```

---

## Step 1: Register Android App in Firebase Console

1. Navigate to the [Firebase Console](https://console.firebase.google.com).
2. Select your existing project: **`veksha-health-monitor`**.
3. Click the gear icon next to Project Overview ➔ **Project settings**.
4. In the **General** tab, scroll down to **Your apps** and click **Add app** (select the **Android** icon).
5. Enter:
   - **Android package name:** `com.veksha.smsgateway`
   - **App nickname:** `VEKSHA SMS Gateway`
   - **Debug signing certificate SHA-1:** (Optional, can be left blank for FCM notifications).
6. Click **Register app**.
7. Download **`google-services.json`**.
8. Place the downloaded file directly inside:
   `android_sms_gateway/app/google-services.json`

> ⚠️ **Security Warning:** `google-services.json` is already excluded in `android_sms_gateway/.gitignore`. Never commit credentials to public version control.

---

## Step 2: Configure Backend Environment Variables

Open `server/.env` and ensure the following keys are set:

```env
ANDROID_GATEWAY_ENABLED=true
FCM_ENABLED=true
GATEWAY_API_KEY=your-chosen-secret-key-1234
```

> **Note:** Choose any secure string for `GATEWAY_API_KEY`. You will enter this exact same key in the Android app.

---

## Step 3: Build & Install the Android App

### Option A: Using Android Studio (Recommended)
1. Open **Android Studio**.
2. Select **Open** and browse to `C:\Users\JAYESH\OneDrive\Desktop\veksha\android_sms_gateway`.
3. Allow Gradle to sync dependencies.
4. Connect your phone via USB with **USB Debugging** enabled (Developer Options).
5. Click **Run 'app'** (green play button) or build the APK via **Build ➔ Build Bundle(s) / APK(s) ➔ Build APK(s)**.

### Option B: Command Line (Gradle)
If Android SDK and Java are configured in your system path:
```powershell
cd C:\Users\JAYESH\OneDrive\Desktop\veksha\android_sms_gateway
.\gradlew assembleDebug
```
The resulting APK will be at:
`android_sms_gateway/app/build/outputs/apk/debug/app-debug.apk`

Transfer this `.apk` to your phone via USB or Google Drive, then tap to install (allow "Install from Unknown Sources").

---

## Step 4: Configure the Android App on Your Phone

1. Open the **VEKSHA SMS Gateway** app on your phone.
2. In the **Configuration** section, fill in:
   - **Backend URL:**
     - For local WiFi testing: `http://<YOUR_PC_LOCAL_IP>:8000` (e.g., `http://192.168.1.15:8000`)
     - For production / remote deployment: `https://your-backend-domain.com`
   - **Gateway API Key:** Enter the same key set in `server/.env` (`GATEWAY_API_KEY`).
   - **Emergency Contacts:** Comma-separated mobile numbers with country code:
     `+918591856635, +919876543210`
   - **Auto-send SMS on alert:** Toggle **ON**.
3. Tap **Save Settings**.

---

## Step 5: Permissions & Background Optimization (Crucial for OnePlus)

1. Tap **Grant Required Permissions**:
   - Allow **Send and view SMS messages**
   - Allow **Notifications**
2. **Disable Battery Optimization:**
   - Tap **Disable Battery Optimization (Recommended)**.
   - Choose "Don't optimize" / "Unrestricted" for VEKSHA SMS Gateway.
3. **OnePlus Specific Settings (OxygenOS / ColorOS):**
   - Go to phone **Settings ➔ Apps ➔ VEKSHA SMS Gateway ➔ Battery usage**:
     - Enable **Allow background activity**.
     - Enable **Allow auto-launch**.
   - Go to **Settings ➔ Battery ➔ More settings ➔ App Quick Freeze**:
     - Ensure **VEKSHA SMS Gateway** is **NOT** frozen.
4. In the app, tap **Register as SMS Gateway**.
   - Status should change to `✅ Registered as SMS Gateway`.
5. Tap **Start Service** to launch the ongoing foreground service notification.

---

## Step 6: Verify End-to-End System

1. Ensure the FastAPI backend server is running:
   ```powershell
   cd C:\Users\JAYESH\OneDrive\Desktop\veksha\server
   .\venv\Scripts\uvicorn.exe main:app --host 0.0.0.0 --port 8000 --reload
   ```
2. Open the dashboard in your browser: `http://localhost:8000/`
3. Notice the **Android SMS Gateway** card and navigation pill:
   - Status pill displays: `Gateway: 1 Device` (green pulse).
4. Click the dashboard's **Emergency SOS** button.
5. Verification sequence:
   - Backend records the SOS event in Firestore.
   - Backend sends high-priority FCM push notification to the registered phone.
   - Android phone receives FCM alert in foreground or background.
   - Phone displays heads-up emergency notification with vibration.
   - Phone `SmsDispatcher` transmits SMS via SIM card to emergency contacts.
   - Phone reports SMS status (`SENT_TO_TELEPHONY` / `DELIVERY_CONFIRMED`) back to the backend.
   - Dashboard automatically updates the SMS status log.

---

## SMS Status Definitions

| Status | Meaning |
|---|---|
| **QUEUED** | Android `SmsManager` accepted the request into the local transmit queue. |
| **SENT_TO_TELEPHONY** | Android radio layer confirmed dispatch to the cellular tower network. |
| **DELIVERY_CONFIRMED** | Carrier network returned an SMS delivery confirmation report. |
| **FAILED** | SIM card failed to transmit (insufficient SMS balance, airplane mode, or network drop). |

