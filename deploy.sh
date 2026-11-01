#!/usr/bin/env bash
#
# AraStars deployment helper.
#
# Sets up the virtualenv, dependencies and .env, then installs and starts the
# three systemd services (backend, client bot, admin bot).
#
# Usage:
#   ./deploy.sh                 full install (venv + deps + .env + systemd)
#   ./deploy.sh --no-systemd    only venv + deps + .env
#   ./deploy.sh --units-only    only the systemd part (run as root when the
#                               project owner has no sudo rights)
#   ./deploy.sh --force         skip the "run as project owner" guard
#
# Safe to re-run: every step is idempotent. Re-run it after `git pull`.

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

SKIP_SYSTEMD=0
UNITS_ONLY=0
FORCE=0
for arg in "$@"; do
    case "$arg" in
        --no-systemd) SKIP_SYSTEMD=1 ;;
        --units-only) UNITS_ONLY=1 ;;
        --force)      FORCE=1 ;;
        -h|--help)    sed -n '2,15p' "${BASH_SOURCE[0]}" | sed 's/^# \?//'; exit 0 ;;
        *)            echo "Unknown option: $arg" >&2; exit 2 ;;
    esac
done

say()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[33m[!] %s\033[0m\n' "$*"; }
die()  { printf '\033[31m[x] %s\033[0m\n' "$*" >&2; exit 1; }

RUN_USER="${RUN_USER:-$(stat -c '%U' "$PROJECT_DIR")}"
RUN_GROUP="$(id -gn "$RUN_USER" 2>/dev/null || echo "$RUN_USER")"
CURRENT_USER="$(id -un)"

if [ "$(id -u)" -ne 0 ]; then SUDO="sudo"; else SUDO=""; fi

install_units() {
    command -v systemctl >/dev/null 2>&1 || die "systemctl not found — use --no-systemd"

    say "Installing systemd units (paths -> $PROJECT_DIR, user -> $RUN_USER)"
    for unit in ara-backend ara-client-bot ara-admin-bot; do
        [ -f "$PROJECT_DIR/$unit.service" ] || die "missing $unit.service in $PROJECT_DIR"
        sed -e "s|/home/tamik/botstars|$PROJECT_DIR|g" \
            -e "s|^User=.*|User=$RUN_USER|" \
            -e "s|^Group=.*|Group=$RUN_GROUP|" \
            "$PROJECT_DIR/$unit.service" \
            | $SUDO tee "/etc/systemd/system/$unit.service" >/dev/null
        echo "    /etc/systemd/system/$unit.service"
    done

    $SUDO systemctl daemon-reload
}

require_root_access() {
    [ -n "$SUDO" ] || return 0
    say "Checking sudo access (systemd needs root)"
    sudo -v && return 0
    warn "sudo refused: either the password was rejected, or '$CURRENT_USER' has no sudo rights."
    warn "Check what is allowed with:  sudo -l"
    cat <<HINT

    Everything except the systemd step is already done.
    Finish it as root:

        su -
        $PROJECT_DIR/deploy.sh --units-only
        systemctl enable --now ara-backend ara-client-bot ara-admin-bot

    Or give this user sudo rights once, as root:

        usermod -aG sudo $RUN_USER      # then log out and back in

HINT
    die "sudo unavailable"
}

if [ "$UNITS_ONLY" -eq 1 ]; then
    install_units
    say "Units installed. Start them with:"
    echo "    systemctl enable --now ara-backend ara-client-bot ara-admin-bot"
    exit 0
fi

if [ "$CURRENT_USER" != "$RUN_USER" ] && [ "$FORCE" -eq 0 ]; then
    warn "Project directory is owned by '$RUN_USER' but you are '$CURRENT_USER'."
    warn "Creating the venv as the wrong user breaks later 'pip install' runs."
    die  "Run it as the owner:  sudo -u $RUN_USER $PROJECT_DIR/deploy.sh"
fi

# ---------------------------------------------------------------- python ----
say "Looking for Python 3.11+"
PY=""
for candidate in python3.13 python3.12 python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1 &&
       "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
        PY="$candidate"
        break
    fi
done
[ -n "$PY" ] || die "Python 3.11+ not found (the code uses 'from datetime import UTC').
    Install it:  sudo apt install python3.12 python3.12-venv"
echo "    using $PY ($("$PY" --version))"

# ------------------------------------------------------------------ venv ----
if [ ! -x "$PROJECT_DIR/venv/bin/python" ]; then
    say "Creating virtualenv"
    "$PY" -m venv venv || die "venv creation failed. Install the venv module:
    sudo apt install ${PY}-venv"
else
    say "Virtualenv already exists, reusing it"
fi

say "Installing dependencies"
./venv/bin/python -m pip install --upgrade pip --quiet
./venv/bin/pip install -r requirements.txt

# ------------------------------------------------------------------- env ----
if [ ! -f .env ]; then
    say "Creating .env from .env.example"
    cp .env.example .env
    warn ".env is empty — fill it in before the bots can start."
else
    say ".env already exists, keeping it"
fi
chmod 600 .env

MISSING=()
for key in BOT_TOKEN ADMIN_BOT_TOKEN ADMIN_CHAT_ID; do
    value="$(grep -E "^${key}=" .env | tail -n1 | cut -d= -f2- || true)"
    value="${value%\"}"; value="${value#\"}"
    value="$(printf '%s' "$value" | tr -d '[:space:]')"
    [ -n "$value" ] || MISSING+=("$key")
done

if [ "${#MISSING[@]}" -gt 0 ]; then
    warn "These required variables are empty in .env: ${MISSING[*]}"
    warn "The services would crash on startup, so they were NOT started."
    echo
    echo "    Fill them in:   nano $PROJECT_DIR/.env"
    echo "    Then re-run:    $PROJECT_DIR/deploy.sh"
    exit 1
fi

if [ "$SKIP_SYSTEMD" -eq 1 ]; then
    say "Done (--no-systemd). Start manually:"
    echo "    cd $PROJECT_DIR && ./venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000"
    echo "    cd $PROJECT_DIR && ./venv/bin/python bot.py"
    echo "    cd $PROJECT_DIR && ./venv/bin/python admin_bot.py"
    exit 0
fi

# --------------------------------------------------------------- systemd ----
require_root_access
install_units

say "Starting services"
$SUDO systemctl enable --now ara-backend
sleep 2
$SUDO systemctl enable --now ara-client-bot ara-admin-bot
sleep 2

say "Backend health check"
if curl -fsS --max-time 5 http://127.0.0.1:8000/health; then
    echo
else
    warn "Backend did not answer on 127.0.0.1:8000 — check: journalctl -u ara-backend -n 50"
fi

say "Status"
$SUDO systemctl --no-pager --lines=0 status ara-backend ara-client-bot ara-admin-bot || true

cat <<INFO

Done. Useful commands:

    journalctl -u ara-backend    -f      backend logs
    journalctl -u ara-client-bot -f      client bot logs
    journalctl -u ara-admin-bot  -f      admin bot logs

    sudo systemctl restart ara-backend ara-client-bot ara-admin-bot

After updating the code:

    cd $PROJECT_DIR && git pull && ./deploy.sh
INFO
