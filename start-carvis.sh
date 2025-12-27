#!/usr/bin/env bash
#
# CarVis — one-command local bootstrap.
#
#   ./start-carvis.sh              start Postgres (if needed), load data (if empty), run the app
#   ./start-carvis.sh --reload     force a fresh ETL run before starting
#   ./start-carvis.sh --no-db      skip Postgres entirely; run on the CSV fallback
#   ./start-carvis.sh --port 8080  serve on a different port
#   ./start-carvis.sh --stop       stop the database container and exit
#
# DESIGN NOTE — this script CHECKS prerequisites, it never installs them.
# Auto-installing Docker or Python would need sudo, differ per distro, and can
# leave a contributor's machine in a state they did not ask for. Every failure
# below exits with the exact command or link needed to fix it.

set -euo pipefail

# --- configuration ----------------------------------------------------------
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$PROJECT_ROOT/.venv"
CONTAINER="carvis-pg"
VOLUME="carvis_pgdata"
IMAGE="postgres:17"
PG_USER="carvis"
PG_PASSWORD="carvis"
PG_DB="carvis"
PG_PORT="5432"
APP_PORT="8000"
DATABASE_URL="postgresql://${PG_USER}:${PG_PASSWORD}@localhost:${PG_PORT}/${PG_DB}"

USE_DB=1
FORCE_RELOAD=0

# --- output helpers ---------------------------------------------------------
if [[ -t 1 ]]; then
    BOLD=$'\033[1m'; DIM=$'\033[2m'; RED=$'\033[31m'; GREEN=$'\033[32m'
    YELLOW=$'\033[33m'; RESET=$'\033[0m'
else
    BOLD=""; DIM=""; RED=""; GREEN=""; YELLOW=""; RESET=""
fi

step() { printf '%s==>%s %s\n' "$BOLD" "$RESET" "$1"; }
ok()   { printf '    %s✓%s %s\n' "$GREEN" "$RESET" "$1"; }
warn() { printf '    %s!%s %s\n' "$YELLOW" "$RESET" "$1"; }
die()  { printf '\n%serror:%s %s\n' "$RED" "$RESET" "$1" >&2; [[ $# -gt 1 ]] && printf '%s%s%s\n' "$DIM" "$2" "$RESET" >&2; exit 1; }

# --- arguments --------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-db)  USE_DB=0; shift ;;
        --reload) FORCE_RELOAD=1; shift ;;
        --port)   APP_PORT="${2:?--port needs a value}"; shift 2 ;;
        --stop)   docker stop "$CONTAINER" >/dev/null 2>&1 && echo "stopped $CONTAINER" || echo "$CONTAINER was not running"; exit 0 ;;
        -h|--help) sed -n '3,14p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) die "unknown option: $1" "Run --help to see the available options." ;;
    esac
done

cd "$PROJECT_ROOT"

# --- 1. Python environment --------------------------------------------------
step "Checking the Python environment"

[[ -x "$VENV/bin/python" ]] || die \
    "no virtual environment at .venv" \
    "Create one, then install dependencies:
    python3 -m venv .venv
    ./.venv/bin/pip install -r requirements.txt"

PY="$VENV/bin/python"
ok "python $("$PY" -c 'import sys; print(".".join(map(str, sys.version_info[:3])))')"

"$PY" -c 'import dash, pandas, plotly' 2>/dev/null || die \
    "core packages missing from .venv" \
    "Install them:  ./.venv/bin/pip install -r requirements.txt"
ok "dash, pandas, plotly"

if (( USE_DB )); then
    "$PY" -c 'import psycopg, psycopg_pool' 2>/dev/null || die \
        "database packages missing from .venv" \
        "Install them:  ./.venv/bin/pip install -r requirements.txt
Or run without a database:  ./start-carvis.sh --no-db"
    ok "psycopg, psycopg_pool"
fi

# --- 2. Database ------------------------------------------------------------
if (( USE_DB )); then
    step "Checking Docker"

    command -v docker >/dev/null 2>&1 || die \
        "docker is not installed" \
        "Install Docker Engine:  https://docs.docker.com/engine/install/
Or run without a database:  ./start-carvis.sh --no-db"

    docker info >/dev/null 2>&1 || die \
        "the Docker daemon is not reachable" \
        "Start it:  sudo systemctl start docker
If you get a permission error, add yourself to the docker group:
    sudo usermod -aG docker \$USER   (then log out and back in)"
    ok "docker $(docker --version | sed 's/Docker version //; s/,.*//')"

    step "Starting PostgreSQL"
    if docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
        ok "container '$CONTAINER' already running"
    elif docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER"; then
        docker start "$CONTAINER" >/dev/null
        ok "started existing container '$CONTAINER'"
    else
        warn "no container yet — creating '$CONTAINER' (first run pulls $IMAGE)"
        docker run -d --name "$CONTAINER" \
            -e POSTGRES_USER="$PG_USER" \
            -e POSTGRES_PASSWORD="$PG_PASSWORD" \
            -e POSTGRES_DB="$PG_DB" \
            -p "${PG_PORT}:5432" \
            -v "${VOLUME}:/var/lib/postgresql/data" \
            "$IMAGE" >/dev/null
        ok "created container '$CONTAINER' (data persists in volume '$VOLUME')"
    fi

    # Poll for readiness rather than sleeping a fixed number of seconds: the
    # container reports listening before the server can actually accept queries,
    # and a fixed sleep is either too short on a cold start or wasted time.
    printf '    waiting for postgres '
    for i in $(seq 1 60); do
        if docker exec "$CONTAINER" pg_isready -U "$PG_USER" -d "$PG_DB" >/dev/null 2>&1; then
            printf '\r'; ok "postgres accepting connections (${i}s)"
            break
        fi
        printf '.'
        sleep 1
        [[ $i -eq 60 ]] && { printf '\n'; die "postgres did not become ready in 60s" \
            "Check the container logs:  docker logs $CONTAINER"; }
    done

    # --- 3. Data ------------------------------------------------------------
    step "Checking the data"
    ROWS="$(docker exec "$CONTAINER" psql -U "$PG_USER" -d "$PG_DB" -tAc \
            'SELECT COUNT(*) FROM fact_car_listing' 2>/dev/null || echo 0)"
    ROWS="${ROWS//[^0-9]/}"; ROWS="${ROWS:-0}"

    if (( FORCE_RELOAD )); then
        warn "--reload given, rebuilding from the CSV"
        DATABASE_URL="$DATABASE_URL" "$PY" -m etl.load
    elif (( ROWS == 0 )); then
        warn "fact table is empty, running the loader"
        DATABASE_URL="$DATABASE_URL" "$PY" -m etl.load
    else
        ok "$(printf "%'d" "$ROWS") rows already loaded  ${DIM}(--reload to rebuild)${RESET}"
    fi

    # Price model: regenerable output, so it is not committed. Train on demand.
    if (( FORCE_RELOAD )) || [[ ! -f "$PROJECT_ROOT/ml/price_model.joblib" ]]; then
        step "Training the price model"
        DATABASE_URL="$DATABASE_URL" "$PY" -m ml.price_model || warn \
            "model training failed; the dashboard will show a message on that chart"
    else
        ok "price model present  ${DIM}(--reload to retrain)${RESET}"
    fi

    export DATABASE_URL
else
    step "Skipping the database (--no-db): the dashboard will read the CSV"
    unset DATABASE_URL || true
fi

# --- 4. Application ---------------------------------------------------------
step "Starting the dashboard"

if command -v ss >/dev/null 2>&1 && ss -ltn 2>/dev/null | grep -q ":${APP_PORT}\b"; then
    die "port ${APP_PORT} is already in use" \
        "Stop whatever is on it, or pick another:  ./start-carvis.sh --port 8080"
fi

echo "    http://127.0.0.1:${APP_PORT}"
echo "    ${DIM}Ctrl-C to stop the app. The database keeps running — ./start-carvis.sh --stop to shut it down.${RESET}"
echo
exec "$VENV/bin/gunicorn" app:server -b "127.0.0.1:${APP_PORT}" --timeout 60
