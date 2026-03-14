#!/bin/sh
set -eu

python3 manage.py migrate
python3 manage.py collectstatic --noinput

python3 manage.py shell <<'PY'
import os
from pathlib import Path

from user.models import User
from user.services import APIKeyService, TeamService, UserService

email = os.environ.get("WATERCRAWL_BOOTSTRAP_EMAIL", "paperbird@local")
password = os.environ.get("WATERCRAWL_BOOTSTRAP_PASSWORD", "Paperbird12345!")
key_name = os.environ.get("WATERCRAWL_BOOTSTRAP_KEY_NAME", "Paperbird")
api_key_path = os.environ.get("WATERCRAWL_BOOTSTRAP_API_KEY_PATH", "/shared/watercrawl_api_key")

user = User.objects.filter(email__iexact=email).first()
if not user:
    user = UserService.install(email=email, password=password).user

team_service = TeamService.create_or_get_default_team(user)
team = team_service.team
api_key = team.api_keys.order_by("created_at").first()
if not api_key:
    api_key = APIKeyService.create_api_key(team=team, name=key_name).api_key

target = Path(api_key_path)
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(str(api_key.key), encoding="utf-8")
target.chmod(0o600)
print(f"Bootstrap API key written to {target}")
PY

exec gunicorn \
  --bind "0.0.0.0:${WATERCRAWL_PORT:-8080}" \
  --workers "${WATERCRAWL_GUNICORN_WORKERS:-2}" \
  --timeout "${WATERCRAWL_GUNICORN_TIMEOUT:-120}" \
  watercrawl.wsgi:application
