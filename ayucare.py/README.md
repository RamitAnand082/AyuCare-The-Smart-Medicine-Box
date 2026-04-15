## Ayucare Demo – Flask + SQLite

Ayucare is a small full‑stack demo built with **Flask**, **SQLite**, and vanilla **HTML/CSS/JS**.
It lets a doctor register/login, manage patients and their medicine schedules, and exposes
simple JSON APIs that can be consumed by an ESP32 (or any IoT device).

### 1. Folder structure

- `run.py` – main entrypoint for local development
- `app/__init__.py` – Flask application factory, SQLite setup, DB schema
- `app/auth_routes.py` – registration, login, logout routes
- `app/dashboard_routes.py` – dashboard, patient CRUD, schedules, live dose status
- `app/api_routes.py` – JSON API endpoints for ESP32
- `app/templates/`
  - `base.html` – shared layout
  - `login.html` – doctor login page
  - `register.html` – registration page with password validation UI
  - `dashboard.html` – doctor dashboard UI
- `app/static/css/styles.css` – responsive styling
- `app/static/js/auth.js` – auth UI (show/hide password, real‑time validation)
- `app/static/js/dashboard.js` – dashboard logic using Fetch API
- `requirements.txt` – Python dependencies

The SQLite database (`ayucare.db`) is created automatically in the Flask `instance` folder
the first time you run the app.

### 2. Setup instructions

1. **Create and activate a virtual environment (recommended)**  
   On Windows (PowerShell):

   ```powershell
   cd "c:\Users\ramit\OneDrive\Desktop\Ayucare(Original)"
   python -m venv .venv
   .venv\Scripts\Activate.ps1
   ```

2. **Install dependencies**

   ```bash
   pip install -r requirements.txt
   ```

3. **Run the development server**

   ```bash
   python run.py
   ```

   The app will start on `http://127.0.0.1:5000/`.

4. **Access the app**
   - Open `cd "c:\Users\ramit\OneDrive\Desktop\Ayucare(Original)"
.venv\Scripts\Activate.ps1
python run.py` in your browser.
   - Register a new doctor account.
   - Log in using your **License ID** and **password**.

### 3. Features overview

- **Auth & registration**
  - Register with: **Name**, **License ID**, **Password**, **Confirm Password**
  - Password rules:
    - 8–12 characters
    - At least 2 digits
    - At least 1 uppercase letter
    - At least 1 special character
  - Passwords are **hashed** with Werkzeug before storage.
  - Show/hide password UI and real‑time validation on the registration page.
  - Login with **License ID + Password**, session‑based auth, logout button.

- **Doctor dashboard**
  - Add patient: name, age, `box_id`
  - View patient list
  - Set medicine schedule flags (morning/afternoon/night) via checkboxes
  - **Set dose times** (24h HH:MM) per slot—defaults: 08:00, 14:00, 20:00
  - Edit schedule in place (updates over Fetch API)
  - Delete patient (cascades related schedule and logs)
  - Live dose status table (Taken / Pending / Missed) with scheduled times.

- **API endpoints (for ESP32 / IoT)**
  - `GET /api/get_schedule/<box_id>`  
    Returns JSON with the patient’s schedule:

    ```json
    {
      "success": true,
      "box_id": "BOX123",
      "patient_name": "John Doe",
      "schedule": {
        "morning": true,
        "afternoon": false,
        "night": true
      },
      "times": {
        "morning": "08:00",
        "afternoon": "14:00",
        "night": "20:00"
      }
    }
    ```

  - `POST /api/update_status`  
    Accepts JSON:

    ```json
    {
      "box_id": "BOX123",
      "dose_time": "morning",
      "status": "taken"
    }
    ```

    This is stored in the `logs` table and reflected in the dashboard’s
    Live Dose Status view.

### 4. Hardware ↔ software (ESP32 + desktop monitor)

The **medicine box monitor** (`medicine_box_monitor.py`) connects your ESP32 (USB serial) to the Ayucare app:

- **Software → hardware (schedule):** When you run the monitor with `--box-id BOX001`, it fetches that patient’s schedule from the dashboard and sends it to the ESP32 over serial. It resyncs every 5 minutes so changes in the dashboard (times, morning/afternoon/night, medicine names) are pushed to the device.

- **Hardware → software (button press):** When the patient takes medicine and presses the button, the ESP32 sends a message (e.g. `succes dose one`). The monitor detects it, logs it, and calls the API so the dashboard shows a notification and updates Live Dose Status.

**Run (example):**
```powershell
python medicine_box_monitor.py COM8 --box-id BOX001
```
Use the same Box ID as in the dashboard. Keep `run.py` running and the dashboard open so notifications appear in the app.

**Serial protocol (for your ESP32 firmware):**

1. **Receive schedule (PC → ESP32)**  
   The monitor sends text lines over serial (115200 baud):
   ```
   SCHEDULE_START
   MORNING=1,08:00,Aspirin
   AFTERNOON=0,14:00,
   NIGHT=1,20:00,Vitamin D
   SCHEDULE_END
   ```
   - `MORNING=1` = slot enabled, `0` = disabled.  
   - Next is time (24h `HH:MM`), then medicine name (no comma in name).  
   Parse these to set your reminder times and which slots are active.

2. **Send “dose taken” (ESP32 → PC)**  
   When the patient presses the button for a dose, send one of these (exact text):
   - `succes dose one` or `dose one` → morning  
   - `succes dose two` or `dose two` → afternoon  
   - `succes dose three` or `dose three` → night  
   Example: `Serial.println("succes dose one");`

**Run the integration test (hardware + software):**  
To check that the app and monitor logic work together, run (no server or device needed):

```powershell
python test_integration.py
```

If all checks pass, you’ll see: `All checks passed: hardware and software work together.`

### 5. Database schema (SQLite)

All tables are created automatically on startup:

- `doctors`
  - `id` (PK)
  - `name`
  - `license_id` (unique)
  - `password_hash`
  - `created_at`

- `patients`
  - `id` (PK)
  - `doctor_id` (FK → doctors.id)
  - `name`
  - `age`
  - `box_id` (unique)
  - `created_at`

- `schedules`
  - `id` (PK)
  - `patient_id` (FK → patients.id)
  - `morning` (0/1)
  - `afternoon` (0/1)
  - `night` (0/1)
  - `morning_time`, `afternoon_time`, `night_time` (24h HH:MM, e.g. "08:00", "14:00", "20:00")
  - `created_at`

- `logs`
  - `id` (PK)
  - `patient_id` (FK → patients.id)
  - `box_id`
  - `dose_time` (`morning` | `afternoon` | `night`)
  - `status` (`taken` | `missed` | `pending`)
  - `logged_at`

### 6. Notes

- All **session‑protected routes** (dashboard and related JSON endpoints)
  require a logged‑in doctor.
- Frontend uses the **Fetch API** for updates and renders alerts using
  small toast notifications in the bottom‑right corner.
- The UI is built with plain HTML/CSS and is **responsive** down to mobile widths.

