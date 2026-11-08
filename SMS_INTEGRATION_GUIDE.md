# VEKSHA - Emergency SMS Integration Guide (India DLT & MSG91)

This guide details the exact steps and requirements to enable live emergency SMS alerts for **VEKSHA** to family members in India using **MSG91** and the TRAI-mandated **DLT (Distributed Ledger Technology)** registration framework.

---

## 1. Indian TRAI Regulations (Why DLT is Required)

Under Telecom Regulatory Authority of India (TRAI) guidelines:
* All promotional and transactional SMS sent to Indian phone numbers must originate from a registered entity with an approved **Header (Sender ID)** and **Content Template**.
* Messages not matching an approved DLT template are automatically dropped by telecom carriers (Airtel, Jio, Vi, BSNL).

---

## 2. Step 1: Principal Entity (DLT) Registration

Register as an Enterprise / Entity on any one Indian DLT portal (telecom carriers share a synchronized blockchain):
* **Jio DLT:** [https://trueconnect.jio.com](https://trueconnect.jio.com)
* **Vilpower (Vodafone Idea):** [https://www.vilpower.in](https://www.vilpower.in)
* **Airtel DLT:** [https://www.airtel.in/business/commercial-communication](https://www.airtel.in/business/commercial-communication)
* **Tata Tele DLT:** [https://telemarketer.tatateleservices.com](https://telemarketer.tatateleservices.com)

**Documents Needed:**
* Business / Organization PAN card, GST / Registration Certificate, or Student Entity ID / Letter.
* Authorized signatory ID.

---

## 3. Step 2: Register Header (Sender ID)

In your DLT portal dashboard:
1. Navigate to **Headers** → **Add Header**.
2. Select **Category:** `Service Implicit` or `Transactional`.
3. Provide a 6-character alphabetic header: e.g., `VEKSHA` (or your chosen brand abbreviation).
4. Submit for carrier approval (usually approved within 24–48 hours).

---

## 4. Step 3: Register Content Template

In your DLT portal dashboard:
1. Navigate to **Templates** → **Add Content Template**.
2. **Template Type:** `Service Implicit` (alerts & transactional notifications).
3. **Template Name:** `VEKSHA_EMERGENCY_ALERT`.
4. **Header:** Select your approved 6-character header (e.g. `VEKSHA`).
5. **Template Message:**
   ```text
   EMERGENCY ALERT: {#var1#} triggered on VEKSHA for node {#var2#}. Vitals: HR {#var3#} BPM, SpO2 {#var4#}%. Reason: {#var5#}. Immediate attention requested.
   ```
   > **Note:** The variable format must match DLT syntax: `{#var#}`.
6. Submit template for approval. Save the **Template ID** (a 19-digit number) once approved.

---

## 5. Step 4: Configure MSG91 Account

1. Sign up / Log in to [msg91.com](https://msg91.com).
2. Go to **SMS** → **DLT Details** → Add your DLT Entity ID, Sender ID, and Approved Template ID.
3. Go to **Flows** → **Create Flow**:
   * Map variables:
     * `var1` → Alert Type (`SOS_MANUAL_ALERT` or `SUSTAINED_ABNORMAL`)
     * `var2` → Hardware Node (`esp32-veksha`)
     * `var3` → Heart Rate (`78`)
     * `var4` → SpO2 (`98.5`)
     * `var5` → Alert Reason
   * Copy the generated **Flow ID** (used as `MSG91_TEMPLATE_ID`).
4. Generate an **AuthKey** under **Settings** → **API Keys**.

---

## 6. Step 5: Enable SMS in VEKSHA `server/.env`

Once your credentials are ready, open `server/.env` and update:

```env
# Activate SMS dispatch
SMS_ENABLED=true
SMS_PROVIDER=msg91

# Your MSG91 Auth Key
MSG91_AUTH_KEY=382947AxYourRealKeyHere

# Your Approved DLT Flow / Template ID
MSG91_TEMPLATE_ID=65a1b2c3d4e5f6...

# Your 6-character Approved Sender ID
MSG91_SENDER_ID=VEKSHA

# Comma-separated recipient phone numbers (including country code)
# Example for India (country code 91):
EMERGENCY_CONTACTS=919876543210,919123456789

# Anti-spam cooldown (5 minutes between SMS alerts)
SMS_COOLDOWN_SECONDS=300
```

---

## 7. Safe Testing Without Live DLT (Mock Mode)

While awaiting DLT approvals:
* Set `SMS_ENABLED=false` or `SMS_PROVIDER=mock`.
* In `mock` mode, the backend simulates SMS delivery, generates structured response logs, tracks cooldowns, and updates Firestore without making network calls or incurring SMS charges.

