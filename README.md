# After The Credits

After The Credits is a Django web app for browsing streamable movies, saving a wishlist, writing journal entries, and generating movie recommendations.

## Requirements

Install these before setting up the project:

- Python 3.12 or newer
- Docker Desktop
- Git
- A TMDb API bearer token
- Optional: a Gmail app password if you want email verification and password reset emails to send

## Setup

Clone the repository and move into the project folder:

```powershell
git clone <repo-url>
cd AfterTheCredits
```

Create and activate a virtual environment:

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

Install the Python dependencies:

```powershell
pip install -r requirements.txt
```

Create a `.env` file in the project root, next to `docker-compose.yml`:

```env
DB_NAME=afterthecredits
DB_USER=postgres
DB_PASSWORD=postgres
DB_HOST=localhost
DB_PORT=5432

TMDB_TOKEN=your_tmdb_bearer_token_here
TMDB_REGION=GB

EMAIL_HOST_USER=your_email@gmail.com
EMAIL_HOST_PASSWORD=your_gmail_app_password_here
```

The email values are only needed for verification and password reset emails. The movie features require `TMDB_TOKEN`.

Start the PostgreSQL database with pgvector:

```powershell
docker compose up -d db
```

Run the Django migrations:

```powershell
cd backend
python manage.py migrate
```

Optional, create an admin user:

```powershell
python manage.py createsuperuser
```

Load movie data from TMDb:

```powershell
python manage.py fetch_movies --pages 5 --list popular --region GB
```

To match another setup as closely as possible, use the same `TMDB_REGION`, `--pages`, `--list`, and `--region` values. Movie and streaming availability data comes from TMDb, so results can change over time if TMDb updates its data.

Run the development server:

```powershell
python manage.py runserver
```

Open the app at:

```text
http://127.0.0.1:8000/
```

The Django admin is available at:

```text
http://127.0.0.1:8000/admin/
```

## Useful Commands

Run tests:

```powershell
python manage.py test
```

Stop the database:

```powershell
docker compose down
```

Stop the database and delete its stored data:

```powershell
docker compose down -v
```

## Notes

- The first recommendation or movie import run may take longer because `sentence-transformers/all-MiniLM-L6-v2` is downloaded locally.
- The database runs in Docker using the `pgvector/pgvector:pg16` image.
- Keep `.env` private. It contains database credentials, the TMDb token, and optional email credentials.
