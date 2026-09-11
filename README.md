# Smart Attendance System — Online Project

ESP8266 + RC522 -> Internet -> Flask on Render -> PostgreSQL on Supabase -> Dashboard

Run `schema.sql` in Supabase SQL Editor.

Render:
Build: `pip install -r requirements.txt`
Start: `gunicorn app:app`

Environment variables:
- `DATABASE_URL` = Supabase PostgreSQL connection string
- `API_KEY` = secret shared with the ESP8266
