CREATE TABLE IF NOT EXISTS students (
    id BIGSERIAL PRIMARY KEY,
    student_id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    class_name TEXT NOT NULL,
    section TEXT NOT NULL,
    rfid_uid TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS attendance (
    id BIGSERIAL PRIMARY KEY,
    student_id TEXT NOT NULL,
    name TEXT NOT NULL,
    class_name TEXT NOT NULL,
    section TEXT NOT NULL,
    date DATE NOT NULL,
    time TIME NOT NULL,
    photo TEXT,
    rfid_uid TEXT
);

CREATE INDEX IF NOT EXISTS idx_attendance_date ON attendance(date);
CREATE INDEX IF NOT EXISTS idx_attendance_student_date ON attendance(student_id, date);
