# Granby Smart Canteen — Render Deployment

## Quick deploy (demo)
1. Create a GitHub repository and upload this project.
2. In Render, choose **New → Web Service** and connect the repository.
3. Render can use the included `render.yaml`, or set:
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `gunicorn app:app`
4. Add a `SECRET_KEY` environment variable (Render's Blueprint generates one automatically).
5. Deploy and open the generated `https://...onrender.com` URL.

## Database warning
The app supports PostgreSQL through `DATABASE_URL` and SQLite as a fallback. For a real school deployment, use PostgreSQL rather than SQLite.

If you deploy on Render's ephemeral filesystem with SQLite, database changes can be lost after a restart/redeploy. Do not rely on the included `smart_canteen.db` for production data.

## PostgreSQL
Create a PostgreSQL database in Render and set its `DATABASE_URL` on the web service. The app automatically converts Render's `postgres://` form to `postgresql+psycopg2://`.

## Uploaded product images and QR files
The app currently writes product uploads to `static/uploads/` and generated QR files to `qr_codes/`. These folders are also ephemeral on a normal Render web service. For production, use persistent/external storage or a Render persistent disk on a plan that supports it.

## Default admin
The seed account is:
- Email: `admin@smartcanteen.local`
- Password: `admin123`

Change the admin password immediately after first login.
