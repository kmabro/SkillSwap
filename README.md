# Skill Exchange Platform

A peer-to-peer web application where users exchange skills without monetary payment.

## Features

- Authentication: register, login, logout
- Profile and availability management
- Skill management: add/remove offered and wanted skills
- Matching engine based on complementary skills
- Request workflow: pending, accepted, rejected, completed
- Ratings and feedback after exchange completion
- Search users by skill with availability filter
- Global AI chatbot assistant for SkillSwap guidance

## Tech Stack

- Frontend: HTML, CSS, JavaScript
- Backend: Flask (Python)
- Database: MySQL (default app fallback supports SQLite for quick testing)

## Project Structure

- app.py
- requirements.txt
- schema.sql
- templates/
- static/css/
- static/js/

## Setup Instructions

1. Create and activate a virtual environment:

```bash
python -m venv .venv
.venv\\Scripts\\activate
```

2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Create MySQL database using schema file:

```bash
mysql -u root -p < schema.sql
```

4. Set environment variables (PowerShell):

```powershell
$env:APP_ENV = "development"
$env:ENABLE_DEV_ENDPOINTS = "true"
$env:DATABASE_URL = "mysql+pymysql://root:your_password@localhost/skill_exchange_platform"
$env:SECRET_KEY = "replace-with-a-secure-random-value"
$env:SUPER_ADMIN_PASSWORD = "replace-with-strong-super-admin-password"
$env:GROQ_API_KEY = "replace-with-your-groq-api-key"
$env:GROQ_MODEL_PRIMARY = "llama-3.1-8b-instant"
$env:GROQ_MODEL_FALLBACKS = "llama3-8b-8192"
```

5. Run the app:

```bash
flask --app app run
```

Then open http://127.0.0.1:5000.

## Security Notes

- Use a local `.env` file for secrets and keep it out of git (the repository now ignores `.env`).
- Required in production: `APP_ENV=production`, `DATABASE_URL`, `SECRET_KEY`, and `SUPER_ADMIN_PASSWORD`.
- Development test endpoints are controlled by `ENABLE_DEV_ENDPOINTS` and are disabled by default in production.
- Rotate keys/passwords immediately if any previous credentials were shared or committed.

## Production Deployment Checklist

1. Set `APP_ENV=production`.
2. Set a strong random `SECRET_KEY` (example: `python -c "import secrets; print(secrets.token_urlsafe(64))"`).
3. Set a production `DATABASE_URL` and verify DB user permissions.
4. Set a strong `SUPER_ADMIN_PASSWORD` and a valid `ADMIN_EMAIL`.
5. Set real provider credentials (`GROQ_API_KEY`, mail credentials, reCAPTCHA keys).
6. Keep `.env` out of git (already ignored) and commit only `.env.example` placeholders.
7. Rotate all credentials if they were previously exposed.

## Deploy To Render (Working Backend Link)

GitHub Pages is static-only and cannot run this Flask + MySQL backend. Use Render for a real working web app URL.

1. Push this repo to GitHub (already done).
2. Open Render dashboard and create a new **Blueprint**.
3. Select this repository; Render will detect `render.yaml` automatically.
4. Set required environment variables in Render:
	- `DATABASE_URL` (production MySQL URL)
	- `SECRET_KEY`
	- `SUPER_ADMIN_PASSWORD`
	- `ADMIN_EMAIL`
	- Mail settings (`MAIL_*`)
	- reCAPTCHA keys
	- `GROQ_API_KEY`
5. Deploy. Render will build with `requirements.txt` and start with gunicorn.
6. Use the generated `https://<service>.onrender.com` URL as your permanent working link.

## Chatbot Integration

- Frontend widget files:
	- `templates/components/chatbot_widget.html`
	- `static/css/chatbot.css`
	- `static/js/chatbot.js`
- Backend endpoint:
	- Unified assistant: `POST /api/chat` with JSON body `{ "message": "..." }`
	- Compatibility alias: `POST /api/chat/user` maps to the same handler
	- Response: `{ "response": "..." }`
- Behavior:
	- Provider: Groq-based assistant for user-side chatbot
	- Typing indicator and error handling
	- Message length limit and request rate limiting

## Optional: Initialize with Flask ORM (if not using schema.sql)

```bash
flask --app app init-db
```
