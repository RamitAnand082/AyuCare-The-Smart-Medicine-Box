from flask import Flask, jsonify, request

app = Flask(__name__)

# Sample database (temporary)
schedule_db = {
    "BOX123": {
        "patient_name": "John Doe",
        "schedule": {
            "morning": True,
            "afternoon": False,
            "night": True
        },
        "medicines": {
            "morning": "Aspirin",
            "afternoon": "",
            "night": "Vitamin D"
        },
        "status": {
            "morning": "pending",
            "afternoon": "pending",
            "night": "pending"
        }
    }
}

# ==============================
# GET SCHEDULE
# ==============================
@app.route("/api/get_schedule/<box_id>", methods=["GET"])
def get_schedule(box_id):
    if box_id not in schedule_db:
        return jsonify({"success": False, "message": "Box not found"}), 404

    data = schedule_db[box_id]

    return jsonify({
        "success": True,
        "box_id": box_id,
        "patient_name": data["patient_name"],
        "schedule": data["schedule"],
        "medicines": data["medicines"],
        "status": data["status"]
    })


# ==============================
# UPDATE STATUS
# ==============================
@app.route("/api/update_status", methods=["POST"])
def update_status():
    data = request.get_json()

    box_id = data.get("box_id")
    dose_time = data.get("dose_time")
    status = data.get("status")

    if not box_id or not dose_time or not status:
        return jsonify({"success": False, "message": "Missing fields"}), 400

    if box_id not in schedule_db:
        return jsonify({"success": False, "message": "Box not found"}), 404

    schedule_db[box_id]["status"][dose_time] = status

    print(f"{dose_time} dose marked {status}")

    return jsonify({
        "success": True,
        "message": "Dose status recorded."
    })


# ==============================
# HOME PAGE
# ==============================
@app.route("/")
def home():
    return "Ayucare server running"


# ==============================
# RUN SERVER
# ==============================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
