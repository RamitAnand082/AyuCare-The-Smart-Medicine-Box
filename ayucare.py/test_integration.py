"""
Integration test: hardware and software work together.

Run: python test_integration.py

This script:
1. Uses the same Flask app and DB to create a test patient (Box ID: BOX_INTEGRATION_TEST).
2. Tests that the API used by the monitor works: get_schedule, update_status.
3. Tests that the monitor's schedule format and dose detection match what the API expects.
4. Cleans up the test patient when done.

You do NOT need run.py running. The test uses the app's test client.
"""

import sys

# Add project root so "from app" works
sys.path.insert(0, ".")


def run_tests():
    from app import create_app
    from app.extensions import db
    from app.models import Doctor, Patient, Schedule, Log

    app = create_app()
    test_box_id = "BOX_INTEGRATION_TEST"

    with app.app_context():
        # ----- 1. Create test data -----
        doctor = Doctor.query.filter_by(license_id="TEST_INTEGRATION_DOC").first()
        if not doctor:
            doctor = Doctor(
                name="Test Integration Doctor",
                license_id="TEST_INTEGRATION_DOC",
            )
            doctor.set_password("TestPass1!")
            db.session.add(doctor)
            db.session.commit()

        patient = Patient.query.filter_by(box_id=test_box_id).first()
        if not patient:
            patient = Patient(
                doctor_id=doctor.id,
                name="Test Patient",
                age=30,
                box_id=test_box_id,
            )
            db.session.add(patient)
            db.session.commit()
            sched = Schedule(
                patient_id=patient.id,
                morning=True,
                afternoon=True,
                night=False,
                morning_time="08:00",
                afternoon_time="14:00",
                night_time="20:00",
                morning_medicine="Aspirin",
                afternoon_medicine="Vitamin D",
                night_medicine="",
            )
            db.session.add(sched)
            db.session.commit()
        else:
            # Ensure schedule exists
            if not patient.schedule:
                sched = Schedule(
                    patient_id=patient.id,
                    morning=True,
                    afternoon=True,
                    night=False,
                    morning_time="08:00",
                    afternoon_time="14:00",
                    night_time="20:00",
                    morning_medicine="Aspirin",
                    afternoon_medicine="Vitamin D",
                    night_medicine="",
                )
                db.session.add(sched)
                db.session.commit()

        client = app.test_client()

        # ----- 2. Test GET /api/get_schedule/<box_id> (software → hardware: monitor fetches this) -----
        r = client.get(f"/api/get_schedule/{test_box_id}")
        if r.status_code != 200:
            print(f"FAIL: get_schedule returned {r.status_code}")
            cleanup(app, test_box_id)
            return False
        data = r.get_json()
        if not data.get("success") or data.get("box_id") != test_box_id:
            print("FAIL: get_schedule response missing success or wrong box_id")
            cleanup(app, test_box_id)
            return False
        if not data.get("schedule", {}).get("morning"):
            print("FAIL: get_schedule should have morning=true")
            cleanup(app, test_box_id)
            return False
        print("  OK  GET /api/get_schedule/<box_id> (schedule for device)")

        # ----- 3. Test monitor's schedule format (what we send to ESP32) -----
        from medicine_box_monitor import build_schedule_lines, get_matched_trigger_and_dose_time

        lines = build_schedule_lines(data)
        if "SCHEDULE_START" not in lines or "SCHEDULE_END" not in lines:
            print("FAIL: build_schedule_lines missing SCHEDULE_START/END")
            cleanup(app, test_box_id)
            return False
        if "MORNING=1" not in lines or "08:00" not in lines:
            print("FAIL: build_schedule_lines should contain MORNING=1 and 08:00")
            cleanup(app, test_box_id)
            return False
        print("  OK  Monitor schedule format (software -> hardware)")

        # ----- 4. Test dose trigger parsing (hardware → software: what ESP32 sends) -----
        trigger, dose_time = get_matched_trigger_and_dose_time("succes dose one")
        if dose_time != "morning":
            print(f"FAIL: 'succes dose one' should map to morning, got {dose_time}")
            cleanup(app, test_box_id)
            return False
        trigger2, dose_time2 = get_matched_trigger_and_dose_time("dose two")
        if dose_time2 != "afternoon":
            print(f"FAIL: 'dose two' should map to afternoon, got {dose_time2}")
            cleanup(app, test_box_id)
            return False
        print("  OK  Dose trigger parsing (hardware -> software)")

        # ----- 5. Test POST /api/update_status (monitor calls this when button pressed) -----
        r = client.post(
            "/api/update_status",
            json={
                "box_id": test_box_id,
                "dose_time": "morning",
                "status": "taken",
            },
            content_type="application/json",
        )
        if r.status_code != 200:
            print(f"FAIL: update_status returned {r.status_code}")
            cleanup(app, test_box_id)
            return False
        body = r.get_json()
        if not body.get("success"):
            print("FAIL: update_status response success not true")
            cleanup(app, test_box_id)
            return False
        # Check that a log was created
        log = Log.query.filter_by(box_id=test_box_id, dose_time="morning", status="taken").order_by(Log.id.desc()).first()
        if not log:
            print("FAIL: update_status did not create a log entry")
            cleanup(app, test_box_id)
            return False
        print("  OK  POST /api/update_status (button press -> dashboard)")

        cleanup(app, test_box_id)
        return True


def cleanup(app, test_box_id: str):
    """Remove test patient and logs so we don't leave junk in the DB."""
    with app.app_context():
        from app.extensions import db
        from app.models import Patient, Log

        patient = Patient.query.filter_by(box_id=test_box_id).first()
        if patient:
            Log.query.filter_by(patient_id=patient.id).delete()
            db.session.delete(patient)
            db.session.commit()


if __name__ == "__main__":
    print("Running integration test (hardware + software)...\n")
    try:
        ok = run_tests()
    except Exception as e:
        print(f"\nFAIL: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    if ok:
        print("\n--- All checks passed: hardware and software work together. ---")
        sys.exit(0)
    else:
        sys.exit(1)
