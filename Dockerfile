# Reconstructed 2026-08-28 -- the image running on the Hetzner box (built
# 2026-08-07) predates this Dockerfile ever being committed to git (see
# HANDOFF.md's "Neither Dockerfile nor the Hetzner-specific
# docker-compose.yml are committed to the repo yet" -- this closes that
# gap for the Dockerfile half). Reconstructed to match the running
# container's own `docker inspect` output exactly: same base image
# (python:3.12.13, slim -- 237MB total matches slim, not the full image),
# same WorkingDir (/app), same Cmd (plain uvicorn, no extra flags), same
# exposed port (8000, mapped to host 8003 by docker-compose.yml). No
# secrets baked in here -- DATABASE_URL/JWT_SECRET_KEY/etc all come from
# docker-compose.yml's `env_file: ./.env` at container-creation time.
#
# Builds two docker-compose services from this same file: `api` (this
# app) and `shadow_runner` (python -m shadow_runner.main, same image,
# different Cmd override in docker-compose.yml).
FROM python:3.12.13-slim

WORKDIR /app

# Layer separated from the full COPY below so `pip install` is only
# re-run when requirements.txt actually changes, not on every code edit.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
