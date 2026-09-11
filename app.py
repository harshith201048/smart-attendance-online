import os
from datetime import datetime
from zoneinfo import ZoneInfo

import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask, jsonify, request, render_template

app = Flask(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL")
API_KEY = os.environ.get("API_KEY", "CHANGE_ME")
IST = ZoneInfo("Asia/Kolkata")


def get_db():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not configured")
    return psycopg2.connect(DATABASE_URL, sslmode="require")


def normalize_uid(uid):
    if uid is None:
        return ""
    return str(uid).strip().upper().replace(" ", "").replace(":", "").replace("-", "")


@app.get("/")
def dashboard():
    return render_template("dashboard.html")


@app.get("/api/health")
def health():
    try:
        conn = get_db()
        conn.close()
        return jsonify({"status": "online", "database": True})
    except Exception as e:
        return jsonify({"status": "error", "database": False, "error": str(e)}), 500


@app.post("/api/scan")
def scan():
    if request.headers.get("X-API-Key") != API_KEY:
        return jsonify({"success": False, "message": "Unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    uid = normalize_uid(data.get("uid"))

    if not uid:
        return jsonify({"success": False, "message": "RFID UID missing"}), 400

    conn = get_db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT student_id, name, class_name, section, rfid_uid
                FROM students
                WHERE UPPER(REPLACE(REPLACE(REPLACE(rfid_uid, ' ', ''), ':', ''), '-', '')) = %s
                LIMIT 1
            """, (uid,))
            student = cur.fetchone()

            if not student:
                return jsonify({
                    "success": False,
                    "known": False,
                    "message": "RFID card is not registered",
                    "rfid_uid": uid
                })

            now = datetime.now(IST)
            date = now.strftime("%Y-%m-%d")
            time = now.strftime("%H:%M:%S")

            cur.execute("""
                SELECT date, time
                FROM attendance
                WHERE student_id = %s AND date = %s
                ORDER BY id DESC
                LIMIT 1
            """, (student["student_id"], date))
            existing = cur.fetchone()

            if existing:
                return jsonify({
                    "success": True,
                    "known": True,
                    "already_present": True,
                    **dict(student),
                    "date": str(existing["date"]),
                    "time": str(existing["time"]),
                    "message": "Attendance already marked"
                })

            cur.execute("""
                INSERT INTO attendance
                    (student_id, name, class_name, section, date, time, photo, rfid_uid)
                VALUES (%s, %s, %s, %s, %s, %s, NULL, %s)
            """, (
                student["student_id"], student["name"], student["class_name"],
                student["section"], date, time, student["rfid_uid"]
            ))
            conn.commit()

            return jsonify({
                "success": True,
                "known": True,
                "already_present": False,
                **dict(student),
                "date": date,
                "time": time,
                "message": "Attendance marked successfully"
            })
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@app.get("/api/stats")
def stats():
    today = datetime.now(IST).strftime("%Y-%m-%d")
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM students")
            total_students = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM attendance WHERE date = %s", (today,))
            today_present = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM attendance")
            total_records = cur.fetchone()[0]
        return jsonify({
            "date": today,
            "total_students": total_students,
            "today_present": today_present,
            "total_attendance_records": total_records
        })
    finally:
        conn.close()


@app.get("/api/today")
def today():
    date = datetime.now(IST).strftime("%Y-%m-%d")
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT id, student_id, name, class_name, section, date, time, rfid_uid
                FROM attendance
                WHERE date = %s
                ORDER BY id DESC
            """, (date,))
            rows = cur.fetchall()
        return jsonify({"date": date, "count": len(rows), "attendance": rows})
    finally:
        conn.close()


@app.get("/api/students")
def students():
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT id, student_id, name, class_name, section, rfid_uid
                FROM students
                ORDER BY class_name, section, name
            """)
            rows = cur.fetchall()
        return jsonify(rows)
    finally:
        conn.close()


@app.post("/api/students")
def add_student():
    if request.headers.get("X-API-Key") != API_KEY:
        return jsonify({"success": False, "message": "Unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    student_id = str(data.get("student_id", "")).strip()
    name = str(data.get("name", "")).strip()
    class_name = str(data.get("class_name", "")).strip()
    section = str(data.get("section", "")).strip()
    rfid_uid = normalize_uid(data.get("rfid_uid"))

    if not all([student_id, name, class_name, section, rfid_uid]):
        return jsonify({"success": False, "message": "All student fields are required"}), 400

    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO students (student_id, name, class_name, section, rfid_uid)
                VALUES (%s, %s, %s, %s, %s)
            """, (student_id, name, class_name, section, rfid_uid))
        conn.commit()
        return jsonify({"success": True, "message": "Student registered"})
    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        return jsonify({"success": False, "message": "Student ID or RFID UID already exists"}), 409
    finally:
        conn.close()


@app.get("/api/attendance")
def attendance():
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT id, student_id, name, class_name, section, date, time, rfid_uid
                FROM attendance
                ORDER BY id DESC
            """)
            rows = cur.fetchall()
        return jsonify(rows)
    finally:
        conn.close()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
