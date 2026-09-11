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


# ==========================================================
# DATABASE
# ==========================================================

def get_db():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not configured")

    return psycopg2.connect(
        DATABASE_URL,
        sslmode="require"
    )


# ==========================================================
# RFID UID NORMALIZATION
# ==========================================================

def normalize_uid(uid):
    if uid is None:
        return ""

    return (
        str(uid)
        .strip()
        .upper()
        .replace(" ", "")
        .replace(":", "")
        .replace("-", "")
    )


# ==========================================================
# API KEY CHECK
# ==========================================================

def authorized():
    return request.headers.get("X-API-Key") == API_KEY


# ==========================================================
# DASHBOARD
# ==========================================================

@app.get("/")
def dashboard():
    return render_template("dashboard.html")


# ==========================================================
# HEALTH
# ==========================================================

@app.get("/api/health")
def health():

    try:
        conn = get_db()
        conn.close()

        return jsonify({
            "status": "online",
            "database": True
        })

    except Exception as e:

        return jsonify({
            "status": "error",
            "database": False,
            "error": str(e)
        }), 500


# ==========================================================
# RFID SCAN FROM ESP8266
# ==========================================================

@app.post("/api/scan")
def scan():

    if not authorized():
        return jsonify({
            "success": False,
            "message": "Unauthorized"
        }), 401

    data = request.get_json(silent=True) or {}

    uid = normalize_uid(data.get("uid"))

    if not uid:
        return jsonify({
            "success": False,
            "message": "RFID UID missing"
        }), 400

    conn = get_db()

    try:

        with conn.cursor(cursor_factory=RealDictCursor) as cur:

            # --------------------------------------------------
            # FIND STUDENT
            # --------------------------------------------------

            cur.execute("""
                SELECT
                    student_id,
                    name,
                    class_name,
                    section,
                    rfid_uid
                FROM students
                WHERE UPPER(
                    REPLACE(
                        REPLACE(
                            REPLACE(rfid_uid, ' ', ''),
                            ':', ''
                        ),
                        '-', ''
                    )
                ) = %s
                LIMIT 1
            """, (uid,))

            student = cur.fetchone()

            # --------------------------------------------------
            # UNKNOWN CARD
            # --------------------------------------------------

            if not student:

                return jsonify({
                    "success": False,
                    "known": False,
                    "already_present": False,
                    "rfid_uid": uid,
                    "message": "RFID card is not registered"
                })

            # --------------------------------------------------
            # CURRENT TIME
            # --------------------------------------------------

            now = datetime.now(IST)

            date = now.strftime("%Y-%m-%d")
            time = now.strftime("%H:%M:%S")

            # --------------------------------------------------
            # DUPLICATE PROTECTION
            # --------------------------------------------------

            cur.execute("""
                SELECT
                    date,
                    time
                FROM attendance
                WHERE student_id = %s
                  AND date = %s
                ORDER BY id DESC
                LIMIT 1
            """, (
                student["student_id"],
                date
            ))

            existing = cur.fetchone()

            if existing:

                return jsonify({
                    "success": True,
                    "known": True,
                    "already_present": True,

                    "student_id": student["student_id"],
                    "name": student["name"],
                    "class_name": student["class_name"],
                    "section": student["section"],
                    "rfid_uid": student["rfid_uid"],

                    "date": str(existing["date"]),
                    "time": str(existing["time"]),

                    "message": "Attendance already marked"
                })

            # --------------------------------------------------
            # INSERT ATTENDANCE
            # --------------------------------------------------

            cur.execute("""
                INSERT INTO attendance
                (
                    student_id,
                    name,
                    class_name,
                    section,
                    date,
                    time,
                    photo,
                    rfid_uid
                )
                VALUES
                (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    NULL,
                    %s
                )
            """, (
                student["student_id"],
                student["name"],
                student["class_name"],
                student["section"],
                date,
                time,
                student["rfid_uid"]
            ))

            conn.commit()

            return jsonify({
                "success": True,
                "known": True,
                "already_present": False,

                "student_id": student["student_id"],
                "name": student["name"],
                "class_name": student["class_name"],
                "section": student["section"],
                "rfid_uid": student["rfid_uid"],

                "date": date,
                "time": time,

                "message": "Attendance marked successfully"
            })

    except Exception:

        conn.rollback()
        raise

    finally:

        conn.close()


# ==========================================================
# STATISTICS
# ==========================================================

@app.get("/api/stats")
def stats():

    today = datetime.now(IST).strftime("%Y-%m-%d")

    conn = get_db()

    try:

        with conn.cursor() as cur:

            # Total students
            cur.execute("""
                SELECT COUNT(*)
                FROM students
            """)

            total_students = cur.fetchone()[0]

            # Present today
            cur.execute("""
                SELECT COUNT(*)
                FROM attendance
                WHERE date = %s
            """, (today,))

            today_present = cur.fetchone()[0]

            # Total attendance records
            cur.execute("""
                SELECT COUNT(*)
                FROM attendance
            """)

            total_records = cur.fetchone()[0]

            # Last scan
            cur.execute("""
                SELECT
                    student_id,
                    name,
                    class_name,
                    section,
                    date,
                    time,
                    rfid_uid
                FROM attendance
                ORDER BY id DESC
                LIMIT 1
            """)

            last_scan = cur.fetchone()

        return jsonify({
            "date": today,
            "total_students": total_students,
            "today_present": today_present,
            "total_attendance_records": total_records,
            "last_scan": last_scan
        })

    finally:

        conn.close()


# ==========================================================
# TODAY'S ATTENDANCE
# ==========================================================

@app.get("/api/today")
def today():

    date = datetime.now(IST).strftime("%Y-%m-%d")

    conn = get_db()

    try:

        with conn.cursor(cursor_factory=RealDictCursor) as cur:

            cur.execute("""
                SELECT
                    id,
                    student_id,
                    name,
                    class_name,
                    section,
                    date,
                    time,
                    rfid_uid
                FROM attendance
                WHERE date = %s
                ORDER BY id DESC
            """, (date,))

            rows = cur.fetchall()

        return jsonify({
            "date": date,
            "count": len(rows),
            "attendance": rows
        })

    finally:

        conn.close()


# ==========================================================
# STUDENT LIST
# ==========================================================

@app.get("/api/students")
def students():

    conn = get_db()

    try:

        with conn.cursor(cursor_factory=RealDictCursor) as cur:

            cur.execute("""
                SELECT
                    id,
                    student_id,
                    name,
                    class_name,
                    section,
                    rfid_uid
                FROM students
                ORDER BY
                    class_name,
                    section,
                    name
            """)

            rows = cur.fetchall()

        return jsonify(rows)

    finally:

        conn.close()


# ==========================================================
# REGISTER STUDENT
# ==========================================================

@app.post("/api/students")
def add_student():

    if not authorized():

        return jsonify({
            "success": False,
            "message": "Unauthorized"
        }), 401

    data = request.get_json(silent=True) or {}

    student_id = str(
        data.get("student_id", "")
    ).strip()

    name = str(
        data.get("name", "")
    ).strip()

    class_name = str(
        data.get("class_name", "")
    ).strip()

    section = str(
        data.get("section", "")
    ).strip()

    rfid_uid = normalize_uid(
        data.get("rfid_uid")
    )

    # ------------------------------------------------------
    # VALIDATION
    # ------------------------------------------------------

    if not all([
        student_id,
        name,
        class_name,
        section,
        rfid_uid
    ]):

        return jsonify({
            "success": False,
            "message": "All student fields are required"
        }), 400

    conn = get_db()

    try:

        with conn.cursor() as cur:

            # --------------------------------------------------
            # CHECK STUDENT ID
            # --------------------------------------------------

            cur.execute("""
                SELECT id
                FROM students
                WHERE student_id = %s
                LIMIT 1
            """, (student_id,))

            if cur.fetchone():

                return jsonify({
                    "success": False,
                    "message": "Student ID already exists"
                }), 409

            # --------------------------------------------------
            # CHECK RFID
            # --------------------------------------------------

            cur.execute("""
                SELECT id
                FROM students
                WHERE UPPER(
                    REPLACE(
                        REPLACE(
                            REPLACE(rfid_uid, ' ', ''),
                            ':', ''
                        ),
                        '-', ''
                    )
                ) = %s
                LIMIT 1
            """, (rfid_uid,))

            if cur.fetchone():

                return jsonify({
                    "success": False,
                    "message": "RFID UID is already registered"
                }), 409

            # --------------------------------------------------
            # INSERT
            # --------------------------------------------------

            cur.execute("""
                INSERT INTO students
                (
                    student_id,
                    name,
                    class_name,
                    section,
                    rfid_uid
                )
                VALUES
                (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s
                )
            """, (
                student_id,
                name,
                class_name,
                section,
                rfid_uid
            ))

        conn.commit()

        return jsonify({
            "success": True,
            "message": "Student registered successfully"
        })

    except Exception:

        conn.rollback()
        raise

    finally:

        conn.close()


# ==========================================================
# DELETE STUDENT
# ==========================================================

@app.delete("/api/students/<student_id>")
def delete_student(student_id):

    if not authorized():

        return jsonify({
            "success": False,
            "message": "Unauthorized"
        }), 401

    student_id = str(student_id).strip()

    if not student_id:

        return jsonify({
            "success": False,
            "message": "Student ID is required"
        }), 400

    conn = get_db()

    try:

        with conn.cursor(cursor_factory=RealDictCursor) as cur:

            # --------------------------------------------------
            # CHECK STUDENT
            # --------------------------------------------------

            cur.execute("""
                SELECT
                    id,
                    student_id,
                    name,
                    class_name,
                    section,
                    rfid_uid
                FROM students
                WHERE student_id = %s
                LIMIT 1
            """, (student_id,))

            student = cur.fetchone()

            if not student:

                return jsonify({
                    "success": False,
                    "message": "Student not found"
                }), 404

            # --------------------------------------------------
            # DELETE ATTENDANCE FIRST
            #
            # This prevents foreign-key problems if attendance
            # references the student.
            # --------------------------------------------------

            cur.execute("""
                DELETE FROM attendance
                WHERE student_id = %s
            """, (student_id,))

            # --------------------------------------------------
            # DELETE STUDENT
            # --------------------------------------------------

            cur.execute("""
                DELETE FROM students
                WHERE student_id = %s
            """, (student_id,))

        conn.commit()

        return jsonify({
            "success": True,
            "message": "Student deleted successfully",
            "student": dict(student)
        })

    except Exception:

        conn.rollback()
        raise

    finally:

        conn.close()


# ==========================================================
# RFID RECOVERY / UNKNOWN CARD
# ==========================================================

@app.post("/api/rfid/recovery")
def rfid_recovery():

    if not authorized():

        return jsonify({
            "success": False,
            "message": "Unauthorized"
        }), 401

    data = request.get_json(silent=True) or {}

    uid = normalize_uid(
        data.get("rfid_uid")
    )

    if not uid:

        return jsonify({
            "success": False,
            "message": "RFID UID is required"
        }), 400

    conn = get_db()

    try:

        with conn.cursor(cursor_factory=RealDictCursor) as cur:

            # --------------------------------------------------
            # CHECK WHETHER UID ALREADY EXISTS
            # --------------------------------------------------

            cur.execute("""
                SELECT
                    student_id,
                    name,
                    class_name,
                    section,
                    rfid_uid
                FROM students
                WHERE UPPER(
                    REPLACE(
                        REPLACE(
                            REPLACE(rfid_uid, ' ', ''),
                            ':', ''
                        ),
                        '-', ''
                    )
                ) = %s
                LIMIT 1
            """, (uid,))

            student = cur.fetchone()

            if student:

                return jsonify({
                    "success": True,
                    "registered": True,
                    "rfid_uid": uid,
                    "student": dict(student),
                    "message": "RFID is already registered"
                })

            return jsonify({
                "success": True,
                "registered": False,
                "rfid_uid": uid,
                "message": "RFID is available for registration"
            })

    finally:

        conn.close()


# ==========================================================
# REGISTER UNKNOWN RFID TO EXISTING STUDENT
# ==========================================================

@app.post("/api/rfid/recovery/register")
def register_recovered_rfid():

    if not authorized():

        return jsonify({
            "success": False,
            "message": "Unauthorized"
        }), 401

    data = request.get_json(silent=True) or {}

    student_id = str(
        data.get("student_id", "")
    ).strip()

    rfid_uid = normalize_uid(
        data.get("rfid_uid")
    )

    if not student_id or not rfid_uid:

        return jsonify({
            "success": False,
            "message": "Student ID and RFID UID are required"
        }), 400

    conn = get_db()

    try:

        with conn.cursor(cursor_factory=RealDictCursor) as cur:

            # --------------------------------------------------
            # FIND STUDENT
            # --------------------------------------------------

            cur.execute("""
                SELECT
                    student_id,
                    name,
                    class_name,
                    section,
                    rfid_uid
                FROM students
                WHERE student_id = %s
                LIMIT 1
            """, (student_id,))

            student = cur.fetchone()

            if not student:

                return jsonify({
                    "success": False,
                    "message": "Student not found"
                }), 404

            # --------------------------------------------------
            # CHECK RFID DUPLICATE
            # --------------------------------------------------

            cur.execute("""
                SELECT
                    student_id,
                    name
                FROM students
                WHERE UPPER(
                    REPLACE(
                        REPLACE(
                            REPLACE(rfid_uid, ' ', ''),
                            ':', ''
                        ),
                        '-', ''
                    )
                ) = %s
                LIMIT 1
            """, (rfid_uid,))

            existing = cur.fetchone()

            if existing and existing["student_id"] != student_id:

                return jsonify({
                    "success": False,
                    "message": (
                        "This RFID is already assigned to "
                        + str(existing["name"])
                    )
                }), 409

            # --------------------------------------------------
            # ASSIGN RFID
            # --------------------------------------------------

            cur.execute("""
                UPDATE students
                SET rfid_uid = %s
                WHERE student_id = %s
            """, (
                rfid_uid,
                student_id
            ))

        conn.commit()

        return jsonify({
            "success": True,
            "message": "RFID registered successfully",
            "student_id": student_id,
            "rfid_uid": rfid_uid
        })

    except Exception:

        conn.rollback()
        raise

    finally:

        conn.close()


# ==========================================================
# ALL ATTENDANCE
# ==========================================================

@app.get("/api/attendance")
def attendance():

    conn = get_db()

    try:

        with conn.cursor(cursor_factory=RealDictCursor) as cur:

            cur.execute("""
                SELECT
                    id,
                    student_id,
                    name,
                    class_name,
                    section,
                    date,
                    time,
                    rfid_uid
                FROM attendance
                ORDER BY id DESC
            """)

            rows = cur.fetchall()

        return jsonify(rows)

    finally:

        conn.close()


# ==========================================================
# START SERVER
# ==========================================================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=int(
            os.environ.get(
                "PORT",
                "5000"
            )
        )
    )
