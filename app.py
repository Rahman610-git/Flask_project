"""Flask web layer for Sasta Sawari. Thin: all rules live in models.py."""

import hmac
import io
import os

from flask import Flask, jsonify, request, send_file
from dotenv import load_dotenv
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.utils import secure_filename

from models import SastaSawariError, build_demo_system

load_dotenv()

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 4 * 1024 * 1024
system = build_demo_system()  # single in-memory platform instance


CARD_TYPES = {
    ".jpg": ("image/jpeg", lambda data: data.startswith(b"\xff\xd8\xff")),
    ".jpeg": ("image/jpeg", lambda data: data.startswith(b"\xff\xd8\xff")),
    ".png": ("image/png", lambda data: data.startswith(b"\x89PNG\r\n\x1a\n")),
    ".pdf": ("application/pdf", lambda data: data.startswith(b"%PDF-")),
}


def _body():
    return request.get_json(silent=True) or {}


def _admin_denial():
    admin_key = os.environ.get("SASTA_ADMIN_KEY", "")
    if not admin_key:
        return jsonify(error="Student-card review is not configured on this server."), 503
    supplied_key = request.headers.get("X-Admin-Key", "")
    if not hmac.compare_digest(supplied_key, admin_key):
        return jsonify(error="Admin authorization required."), 403
    return None


@app.errorhandler(SastaSawariError)
@app.errorhandler(ValueError)
@app.errorhandler(KeyError)
def handle_error(err):
    message = f"Missing field: {err}" if isinstance(err, KeyError) else str(err)
    return jsonify(error=message), 400


@app.errorhandler(RequestEntityTooLarge)
def handle_upload_too_large(_err):
    return jsonify(error="Student card must be 4 MB or smaller."), 413


@app.get("/")
def index():
    return send_file(os.path.join(app.root_path, "index.html"))


@app.get("/api/state")
def state():
    return jsonify(system.snapshot())


@app.get("/health")
def health():
    return jsonify(status="ok")


@app.post("/api/wallet/topup")
def topup():
    body = _body()
    system.topup(body["rider_id"], float(body["amount"]))
    return jsonify(system.snapshot())


@app.post("/api/rides")
def book():
    body = _body()
    system.book_ride(
        body["rider_id"],
        body["pickup"],
        body["dropoff"],
        body["service"],
        body.get("payment_method", "wallet"),
        body.get("pickup_details", ""),
        body.get("dropoff_details", ""),
    )
    return jsonify(system.snapshot())


@app.post("/api/student-cards")
def submit_student_card():
    rider_id = request.form.get("rider_id", "")
    uploaded_file = request.files.get("student_card")
    if not uploaded_file or not uploaded_file.filename:
        return jsonify(error="Choose a student card file to upload."), 400

    filename = secure_filename(uploaded_file.filename)
    extension = os.path.splitext(filename)[1].lower()
    card_type = CARD_TYPES.get(extension)
    if not card_type:
        return jsonify(error="Upload a JPG, PNG, or PDF student card."), 400
    content = uploaded_file.read(app.config["MAX_CONTENT_LENGTH"] + 1)
    if len(content) > app.config["MAX_CONTENT_LENGTH"]:
        return jsonify(error="Student card must be 4 MB or smaller."), 413
    content_type, is_valid_file = card_type
    if not is_valid_file(content):
        return jsonify(error="The uploaded file does not match its file type."), 400

    system.submit_student_card(rider_id, filename, content_type, content)
    return jsonify(message="Student card submitted for admin review.")


@app.get("/api/admin/student-cards")
def pending_student_cards():
    denial = _admin_denial()
    if denial:
        return denial
    return jsonify(applications=system.pending_student_cards())


@app.get("/api/admin/student-cards/<rider_id>/file")
def student_card_file(rider_id):
    denial = _admin_denial()
    if denial:
        return denial
    card = system.student_card_file(rider_id)
    response = send_file(
        io.BytesIO(card["content"]),
        mimetype=card["content_type"],
        download_name=card["filename"],
        as_attachment=True,
        max_age=0,
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.post("/api/admin/student-cards/<rider_id>/review")
def review_student_card(rider_id):
    denial = _admin_denial()
    if denial:
        return denial
    approved = _body().get("approved")
    if not isinstance(approved, bool):
        return jsonify(error="Review decision must be true or false."), 400
    system.review_student_card(rider_id, approved)
    return jsonify(message="Student card approved." if approved else "Student card rejected.")


@app.post("/api/rides/<ride_id>/<action>")
def ride_action(ride_id, action):
    if action == "accept":
        system.accept_ride(ride_id, _body().get("captain_id"))
    elif action == "start":
        system.start_ride(ride_id)
    elif action == "complete":
        system.complete_ride(ride_id)
    elif action == "cancel":
        system.cancel_ride(ride_id)
    else:
        return jsonify(error=f"Unknown action '{action}'"), 404
    return jsonify(system.snapshot())


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "5000")),
        debug=False,
    )
