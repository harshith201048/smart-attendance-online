import os
import hmac
from datetime import datetime
from zoneinfo import ZoneInfo

import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask, jsonify, request, render_template


app = Flask(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL")
API_KEY = os.environ.get("API_KEY", "")
ADMIN_KEY = os.environ.get("ADMIN_KEY", "")

IST = ZoneInfo("Asia/Kolkata")


# ============================================================
# DATABASE
# ============================================================

def get_db():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not configured")

    return psycopg2.connect(
        DATABASE_URL,
        sslmode="require"
    )


def setup_database():
    """
    Safely prepares the small additional table required for
    automatic RFID recovery.

    Existing students and attendance are NOT deleted.
    """

    conn = get_db()

    try:
        with conn.cursor() as cur:

            # ------------------------------------------------
            # Allow a student to exist before an RFID is assigned.
            # NULL means "RFID not assigned yet".
            # ------------------------------------------------
            cur.execute("""
                ALTER TABLE students
                ALTER COLUMN rfid_uid DROP NOT NULL
            """)

            # ------------------------------------------------
            # Some older databases may not have attendance.rfid_uid
            # ------------------------------------------------
            cur.execute("""
                ALTER TABLE attendance
                ADD COLUMN IF NOT EXISTS rfid_uid TEXT
            """)

            # ------------------------------------------------
            # Automatic RFID recovery table
            # ------------------------------------------------
            cur.execute("""
                CREATE TABLE IF NOT EXISTS rfid_recovery (
                    id BIGSERIAL PRIMARY KEY,
                    rfid_uid TEXT NOT NULL UNIQUE,
                    scanned_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)

        conn.commit()

    finally:
        conn.close()


# ============================================================
# AUTHENTICATION
# ============================================================

def valid_api_key():
    supplied = request.headers.get("X-API-Key", "")

    return bool(API_KEY) and hmac.compare_digest(
        supplied,
        API_KEY
    )


def valid_admin_key():
    supplied = request.headers.get("X-Admin-Key", "")

    return bool(ADMIN_KEY) and hmac.compare_digest(
        supplied,
        ADMIN_KEY
    )


# ============================================================
# RFID NORMALIZATION
# ============================================================

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


def display_uid(uid):
    """
    Converts:

        530E2B29

    into:

        53 0E 2B 29
    """

    uid = normalize_uid(uid)

    if not uid:
        return ""

    if len(uid) % 2 == 0:
        return " ".join(
            uid[i:i + 2]
            for i in range(0, len(uid), 2)
        )

    return uid


# ============================================================
# STARTUP
# ============================================================

try:
    setup_database()
except Exception as startup_error:
    print("Database setup warning:", startup_error)


# ============================================================
# MAIN PAGE
# ============================================================

@app.get("/")
def dashboard():
    return render_template("dashboard.html")


# ============================================================
# HEALTH
# ============================================================

@app.get("/api/health")
def health():

    try:
        conn = get_db()

        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()

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


# ============================================================
# ESP8266 RFID SCAN
# ============================================================

@app.post("/api/scan")
def scan():

    if not valid_api_key():
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

        with conn.cursor(
            cursor_factory=RealDictCursor
        ) as cur:

            # ------------------------------------------------
            # Find student
            # ------------------------------------------------
            cur.execute("""
                SELECT
                    id,
                    student_id,
                    name,
                    class_name,
                    section,
                    rfid_uid
                FROM students
                WHERE UPPER(
                    REPLACE(
                        REPLACE(
                            REPLACE(COALESCE(rfid_uid, ''), ' ', ''),
                            ':',
                            ''
                        ),
                        '-',
                        ''
                    )
                ) = %s
                LIMIT 1
            """, (uid,))

            student = cur.fetchone()

            # =================================================
            # UNKNOWN RFID
            # =================================================

            if not student:

                # Save the unknown RFID automatically.
                cur.execute("""
                    INSERT INTO rfid_recovery
                        (rfid_uid, scanned_at)
                    VALUES
                        (%s, CURRENT_TIMESTAMP)
                    ON CONFLICT (rfid_uid)
                    DO UPDATE SET
                        scanned_at = CURRENT_TIMESTAMP
                """, (uid,))

                conn.commit()

                return jsonify({
                    "success": True,
                    "known": False,
                    "recovery": True,
                    "rfid_uid": uid,
                    "display_uid": display_uid(uid),
                    "message": "Unknown RFID detected"
                })


            # =================================================
            # KNOWN RFID
            # =================================================

            now = datetime.now(IST)

            date_text = now.strftime("%Y-%m-%d")
            time_text = now.strftime("%H:%M:%S")

            # ------------------------------------------------
            # Check whether already present today
            # ------------------------------------------------
            cur.execute("""
                SELECT
                    id,
                    date,
                    time
                FROM attendance
                WHERE student_id = %s
                  AND date = %s
                ORDER BY id DESC
                LIMIT 1
            """, (
                student["student_id"],
                date_text
            ))

            existing = cur.fetchone()

            if existing:

                conn.commit()

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


            # ------------------------------------------------
            # Mark attendance
            # ------------------------------------------------
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
                date_text,
                time_text,
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

                "date": date_text,
                "time": time_text,

                "message": "Attendance marked successfully"
            })

    except Exception:

        conn.rollback()
        raise

    finally:
        conn.close()


# ============================================================
# LAST UNKNOWN RFID
# ============================================================

@app.get("/api/rfid/last-unknown")
def last_unknown_rfid():

    if not valid_admin_key():
        return jsonify({
            "success": False,
            "message": "Unauthorized"
        }), 401

    conn = get_db()

    try:

        with conn.cursor(
            cursor_factory=RealDictCursor
        ) as cur:

            cur.execute("""
                SELECT
                    id,
                    rfid_uid,
                    scanned_at
                FROM rfid_recovery
                ORDER BY id DESC
                LIMIT 1
            """)

            row = cur.fetchone()

            if not row:
                return jsonify({
                    "success": True,
                    "found": False
                })

            return jsonify({
                "success": True,
                "found": True,
                "id": row["id"],
                "rfid_uid": row["rfid_uid"],
                "display_uid": display_uid(row["rfid_uid"]),
                "scanned_at": row["scanned_at"].isoformat()
            })

    finally:
        conn.close()


# ============================================================
# CLEAR UNKNOWN RFID
# ============================================================

@app.post("/api/rfid/recovery/clear")
def clear_recovery():

    if not valid_admin_key():
        return jsonify({
            "success": False,
            "message": "Unauthorized"
        }), 401

    data = request.get_json(silent=True) or {}

    uid = normalize_uid(data.get("rfid_uid"))

    if not uid:
        return jsonify({
            "success": False,
            "message": "RFID UID missing"
        }), 400

    conn = get_db()

    try:

        with conn.cursor() as cur:

            cur.execute("""
                DELETE FROM rfid_recovery
                WHERE rfid_uid = %s
            """, (uid,))

        conn.commit()

        return jsonify({
            "success": True,
            "message": "Recovery record cleared"
        })

    finally:
        conn.close()


# ============================================================
# REGISTER RFID TO EXISTING STUDENT
# ============================================================

@app.post("/api/rfid/recovery/register")
def register_recovered_rfid():

    if not valid_admin_key():
        return jsonify({
            "success": False,
            "message": "Unauthorized"
        }), 401

    data = request.get_json(silent=True) or {}

    uid = normalize_uid(data.get("rfid_uid"))
    student_id = str(
        data.get("student_id", "")
    ).strip()

    if not uid or not student_id:
        return jsonify({
            "success": False,
            "message": "RFID UID and Student ID are required"
        }), 400

    conn = get_db()

    try:

        with conn.cursor(
            cursor_factory=RealDictCursor
        ) as cur:

            # ------------------------------------------------
            # Make sure student exists
            # ------------------------------------------------
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


            # ------------------------------------------------
            # Do not overwrite an existing RFID accidentally.
            # ------------------------------------------------
            if student["rfid_uid"]:

                existing_uid = normalize_uid(
                    student["rfid_uid"]
                )

                if existing_uid != uid:

                    return jsonify({
                        "success": False,
                        "message":
                            "This student already has an RFID. "
                            "Delete/change the existing RFID first."
                    }), 409


            # ------------------------------------------------
            # Make sure this RFID isn't assigned elsewhere.
            # ------------------------------------------------
            cur.execute("""
                SELECT
                    student_id,
                    name
                FROM students
                WHERE UPPER(
                    REPLACE(
                        REPLACE(
                            REPLACE(COALESCE(rfid_uid, ''), ' ', ''),
                            ':',
                            ''
                        ),
                        '-',
                        ''
                    )
                ) = %s
                  AND student_id <> %s
                LIMIT 1
            """, (
                uid,
                student_id
            ))

            other = cur.fetchone()

            if other:

                return jsonify({
                    "success": False,
                    "message":
                        f"This RFID is already registered "
                        f"to {other['name']} "
                        f"({other['student_id']})."
                }), 409


            # ------------------------------------------------
            # Assign RFID
            # ------------------------------------------------
            cur.execute("""
                UPDATE students
                SET rfid_uid = %s
                WHERE student_id = %s
            """, (
                uid,
                student_id
            ))


            # ------------------------------------------------
            # Remove from recovery queue
            # ------------------------------------------------
            cur.execute("""
                DELETE FROM rfid_recovery
                WHERE rfid_uid = %s
            """, (uid,))


        conn.commit()

        return jsonify({
            "success": True,
            "message": "RFID registered successfully",
            "rfid_uid": uid,
            "display_uid": display_uid(uid),
            "student_id": student["student_id"],
            "name": student["name"],
            "class_name": student["class_name"],
            "section": student["section"]
        })

    except Exception:

        conn.rollback()
        raise

    finally:
        conn.close()


# ============================================================
# STATISTICS
# ============================================================

@app.get("/api/stats")
def stats():

    conn = get_db()

    try:

        today_text = datetime.now(
            IST
        ).strftime("%Y-%m-%d")

        with conn.cursor(
            cursor_factory=RealDictCursor
        ) as cur:

            cur.execute("""
                SELECT COUNT(*) AS count
                FROM students
            """)

            total_students = cur.fetchone()["count"]


            cur.execute("""
                SELECT COUNT(*) AS count
                FROM attendance
                WHERE date = %s
            """, (today_text,))

            today_present = cur.fetchone()["count"]


            cur.execute("""
                SELECT COUNT(*) AS count
                FROM attendance
            """)

            total_records = cur.fetchone()["count"]


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
            "date": today_text,
            "total_students": total_students,
            "today_present": today_present,
            "total_attendance_records": total_records,
            "last_scan": last_scan
        })

    finally:
        conn.close()


# ============================================================
# TODAY ATTENDANCE
# ============================================================

@app.get("/api/today")
def today():

    date_text = datetime.now(
        IST
    ).strftime("%Y-%m-%d")

    conn = get_db()

    try:

        with conn.cursor(
            cursor_factory=RealDictCursor
        ) as cur:

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
            """, (date_text,))

            rows = cur.fetchall()

        return jsonify({
            "date": date_text,
            "count": len(rows),
            "attendance": rows
        })

    finally:
        conn.close()


# ============================================================
# ALL ATTENDANCE
# ============================================================

@app.get("/api/attendance")
def attendance():

    if not valid_admin_key():
        return jsonify({
            "success": False,
            "message": "Unauthorized"
        }), 401

    conn = get_db()

    try:

        with conn.cursor(
            cursor_factory=RealDictCursor
        ) as cur:

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


# ============================================================
# STUDENTS
# ============================================================

@app.get("/api/students")
def students():

    if not valid_admin_key():
        return jsonify({
            "success": False,
            "message": "Unauthorized"
        }), 401

    conn = get_db()

    try:

        with conn.cursor(
            cursor_factory=RealDictCursor
        ) as cur:

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


# ============================================================
# ADD STUDENT
# ============================================================

@app.post("/api/students")
def add_student():

    if not valid_admin_key():
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

    # RFID is OPTIONAL during student creation.
    # It can be assigned automatically later by scanning.
    rfid_uid = normalize_uid(
        data.get("rfid_uid")
    )

    if not all([
        student_id,
        name,
        class_name,
        section
    ]):

        return jsonify({
            "success": False,
            "message":
                "Student ID, name, class and section are required"
        }), 400


    conn = get_db()

    try:

        with conn.cursor() as cur:

            # ------------------------------------------------
            # Student ID duplicate
            # ------------------------------------------------
            cur.execute("""
                SELECT 1
                FROM students
                WHERE student_id = %s
                LIMIT 1
            """, (student_id,))

            if cur.fetchone():

                return jsonify({
                    "success": False,
                    "message": "Student ID already exists"
                }), 409


            # ------------------------------------------------
            # RFID duplicate
            # ------------------------------------------------
            if rfid_uid:

                cur.execute("""
                    SELECT 1
                    FROM students
                    WHERE UPPER(
                        REPLACE(
                            REPLACE(
                                REPLACE(
                                    COALESCE(rfid_uid, ''),
                                    ' ',
                                    ''
                                ),
                                ':',
                                ''
                            ),
                            '-',
                            ''
                        )
                    ) = %s
                    LIMIT 1
                """, (rfid_uid,))

                if cur.fetchone():

                    return jsonify({
                        "success": False,
                        "message":
                            "RFID UID is already registered"
                    }), 409


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
                rfid_uid if rfid_uid else None
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


# ============================================================
# DELETE STUDENT
# ============================================================

@app.delete("/api/students/<student_id>")
def delete_student(student_id):

    if not valid_admin_key():
        return jsonify({
            "success": False,
            "message": "Unauthorized"
        }), 401

    student_id = str(
        student_id
    ).strip()

    conn = get_db()

    try:

        with conn.cursor() as cur:

            cur.execute("""
                SELECT id
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


            # Remove attendance first so databases with
            # foreign-key constraints can delete the student.
            cur.execute("""
                DELETE FROM attendance
                WHERE student_id = %s
            """, (student_id,))


            cur.execute("""
                DELETE FROM students
                WHERE student_id = %s
            """, (student_id,))


        conn.commit()

        return jsonify({
            "success": True,
            "message": "Student deleted"
        })

    except Exception:

        conn.rollback()
        raise

    finally:
        conn.close()


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=int(
            os.environ.get("PORT", "5000")
        )
    )
