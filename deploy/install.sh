#!/usr/bin/env bash
#
# One-shot installer for a fresh Debian/Ubuntu box.
#
# Written to be driven from a phone over SSH: it asks a handful of short
# questions, generates the access token itself, and prints a tap-to-configure
# link at the end so nothing long has to be typed on a touch keyboard.
#
# If the repository is private, fetching this script and cloning both need a
# GitHub token with read access to it:
#
#   ssh root@<server-ip>
#   export GH_TOKEN=github_pat_...
#   curl -fsSL -H "Authorization: Bearer $GH_TOKEN" \
#     https://raw.githubusercontent.com/LilOsi45/tracker/claude/solana-memecoin-trading-igtdv3/deploy/install.sh \
#     | GH_TOKEN=$GH_TOKEN bash
#
# If the repository is public, drop the header and GH_TOKEN entirely.
#
# Re-running it is safe: it updates the checkout and keeps the existing
# .env, so your token and keys survive.

set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/LilOsi45/tracker}"
REPO_BRANCH="${REPO_BRANCH:-claude/solana-memecoin-trading-igtdv3}"
APP_DIR="${APP_DIR:-/opt/tracker}"
APP_USER="tracker"

# --------------------------------------------------------------------------

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
info() { printf '  %s\n' "$*"; }
warn() { printf '\033[33m  ! %s\033[0m\n' "$*"; }
die() { printf '\033[31m  x %s\033[0m\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "Run this as root (sudo -i)."
command -v apt-get >/dev/null || die "Expects Debian or Ubuntu."

# The pinned dependencies ship wheels up to cp313. On a newer interpreter pip
# falls back to building from source, which needs Rust for pydantic-core and a
# C toolchain for uvloop/httptools/watchfiles — neither of which this script
# installs. Better to stop here than to leave a half-built venv behind.
if command -v python3 >/dev/null; then
  PY_MINOR="$(python3 -c 'import sys; print(sys.version_info[1])')"
  if (( PY_MINOR > 13 )); then
    die "Found Python 3.$PY_MINOR, supported is 3.9 to 3.13.
     Rebuild the server with Ubuntu 24.04 LTS, which ships Python 3.12."
  fi
  if (( PY_MINOR < 9 )); then
    die "Python 3.$PY_MINOR is too old. Use Ubuntu 24.04 LTS."
  fi
fi

# Prompts must read from the terminal, not from the piped script body.
if [[ -t 0 ]]; then TTY=/dev/stdin; else TTY=/dev/tty; fi

# Strip surrounding whitespace and any carriage return. A value pasted from a
# phone regularly carries a trailing space or a CR, and an API key with an
# invisible \r appended fails later as a 401 that looks like a wrong key.
trim() {
  local s="${1//$'\r'/}"
  s="${s#"${s%%[![:space:]]*}"}"
  printf '%s' "${s%"${s##*[![:space:]]}"}"
}

ask() {
  local prompt="$1" default="${2:-}" answer=""
  if [[ -r $TTY ]]; then
    if [[ -n $default ]]; then
      read -r -p "  $prompt [$default]: " answer <"$TTY" || true
    else
      read -r -p "  $prompt: " answer <"$TTY" || true
    fi
  fi
  answer="$(trim "$answer")"
  printf '%s' "${answer:-$default}"
}

# --------------------------------------------------------------------------
bold ""
bold "Tracker — install"
bold ""

# Every answer can be supplied as an environment variable instead. On a phone
# that matters: one pasted line beats four prompts where a paste without a
# trailing Return looks exactly like a hung script.
DOMAIN="$(trim "${DOMAIN:-}")"
[[ -n $DOMAIN ]] || DOMAIN="$(ask 'Domain (e.g. tracker.your-domain.com)')"
[[ -n $DOMAIN ]] || die "No domain means no certificate and no installable app.
     Or set it beforehand:  export DOMAIN=tracker.your-domain.com"

HELIUS_KEY="$(trim "${HELIUS_KEY:-}")"
[[ -n $HELIUS_KEY ]] || HELIUS_KEY="$(ask 'Helius API key (may be left empty, then no wallet sync)')"

WALLET="$(trim "${WALLET:-}")"
[[ -n $WALLET ]] || WALLET="$(ask 'Solana wallet address (optional)')"

EMAIL="$(trim "${EMAIL:-}")"
[[ -n $EMAIL ]] || EMAIL="$(ask "Email for Let's Encrypt expiry notices" "admin@${DOMAIN#*.}")"

# --- port selection -------------------------------------------------------
# This box may already run something on 8000. Binding there anyway would
# leave the tracker dead with "address already in use", so take the first
# free port instead — and on a re-run keep whatever the unit already uses,
# since the running tracker occupies its own port.
# Tested by actually binding, not by parsing `ss` output: if ss were missing
# its error would be swallowed and every port would look free, which is the
# exact failure this function exists to prevent.
pick_port() {
  python3 - <<'PY'
import socket

for port in range(8000, 8100):
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            continue
    print(port)
    break
else:
    raise SystemExit("no free port between 8000 and 8099")
PY
}

PORT=""
if [[ -f /etc/systemd/system/tracker.service ]]; then
  PORT="$(grep -oP -- '(?<=--port )\d+' /etc/systemd/system/tracker.service || true)"
fi

bold ""
info "Domain:  $DOMAIN"
# Length and first characters, so a truncated or half-pasted key is visible
# here rather than showing up later as an unexplained 401.
if [[ -n $HELIUS_KEY ]]; then
  info "Helius:  ${HELIUS_KEY:0:4}… (${#HELIUS_KEY} chars)"
else
  info "Helius:  missing — coin list works, wallet sync does not"
fi
info "Wallet:  ${WALLET:-not set}"
bold ""

# --- DNS sanity check -----------------------------------------------------
# certbot will fail with a confusing error if the record is missing or still
# points somewhere else, so check before spending the attempt.
PUBLIC_IP="$(curl -fsS --max-time 10 https://api.ipify.org 2>/dev/null || true)"
RESOLVED="$(getent ahostsv4 "$DOMAIN" 2>/dev/null | awk 'NR==1{print $1}' || true)"

if [[ -z $RESOLVED ]]; then
  warn "$DOMAIN does not resolve. Add the A record, then run this script again."
  warn "Until then the certificate step will fail."
elif [[ -n $PUBLIC_IP && $RESOLVED != "$PUBLIC_IP" ]]; then
  warn "$DOMAIN points at $RESOLVED, this server is $PUBLIC_IP."
  warn "certbot will fail while that is the case."
else
  info "DNS points here correctly ($RESOLVED)."
fi

# --- packages -------------------------------------------------------------
bold ""
bold "Packages"
# These two commands are silent for up to two minutes. Say so, otherwise the
# quiet stretch is indistinguishable from a hang.
info "downloading, this takes one to two minutes with no output …"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git python3 python3-venv python3-pip nginx certbot \
  python3-certbot-nginx curl ca-certificates iproute2 openssl >/dev/null
info "installed"

# ss is available now, so a free port can be chosen.
if [[ -n $PORT ]]; then
  info "Reusing port $PORT from the existing installation"
else
  PORT="$(pick_port)"
  [[ $PORT == 8000 ]] && info "Port $PORT" \
    || warn "Port 8000 is taken, tracker runs on $PORT"
fi

# --- user and checkout ----------------------------------------------------
bold ""
bold "Application"

id -u "$APP_USER" >/dev/null 2>&1 || \
  useradd --system --no-create-home --shell /usr/sbin/nologin "$APP_USER"

# The token is only ever used for the transfer itself. It is never written
# into .git/config, so it does not sit on the server afterwards — the cost is
# that a later re-run needs GH_TOKEN again.
AUTH_URL="$REPO_URL"
if [[ -n ${GH_TOKEN:-} ]]; then
  AUTH_URL="https://x-access-token:${GH_TOKEN}@${REPO_URL#https://}"
fi

if [[ -d $APP_DIR/.git ]]; then
  git -C "$APP_DIR" fetch --quiet "$AUTH_URL" "$REPO_BRANCH" \
    || die "Fetch failed. Set GH_TOKEN if the repository is private."
  git -C "$APP_DIR" checkout --quiet -B "$REPO_BRANCH" FETCH_HEAD
  info "Checkout updated"
else
  git clone --quiet --branch "$REPO_BRANCH" "$AUTH_URL" "$APP_DIR" \
    || die "Clone failed. Is the repository private? Then set GH_TOKEN."
  # Strip the credential back out of the stored remote.
  git -C "$APP_DIR" remote set-url origin "$REPO_URL"
  info "Repository cloned"
fi

cd "$APP_DIR"
python3 -m venv backend/.venv
backend/.venv/bin/pip install --quiet --upgrade pip
backend/.venv/bin/pip install --quiet -r backend/requirements.txt
info "Dependencies installed"

install -o "$APP_USER" -g "$APP_USER" -d "$APP_DIR/data"

# --- configuration --------------------------------------------------------
# An existing token is reused so a re-run does not lock the phone out.
if [[ -f .env ]] && grep -q '^ACCESS_TOKEN=.\+' .env; then
  ACCESS_TOKEN="$(grep '^ACCESS_TOKEN=' .env | cut -d= -f2-)"
  info "Reusing the existing access token"
else
  ACCESS_TOKEN="$(openssl rand -hex 24)"
  info "Access token generated"
fi

cat > .env <<EOF
HELIUS_API_KEY=$HELIUS_KEY
RUGCHECK_API_KEY=
DISCORD_WEBHOOK_URL=
ACCESS_TOKEN=$ACCESS_TOKEN
CORS_ORIGINS=https://$DOMAIN
EOF
chown "$APP_USER:$APP_USER" .env
chmod 600 .env

if [[ ! -f config.yaml ]]; then
  cp config.example.yaml config.yaml
  if [[ -n $WALLET ]]; then
    # Turn the empty `wallets: []` into a one-entry list.
    sed -i "s|^  wallets: \[\]|  wallets:\n    - $WALLET|" config.yaml
  fi
  info "config.yaml created"
else
  info "config.yaml exists, left untouched"
fi
chown "$APP_USER:$APP_USER" config.yaml

# --- service --------------------------------------------------------------
bold ""
bold "Service"
sed "s/--port 8000/--port $PORT/" deploy/tracker.service > /etc/systemd/system/tracker.service
systemctl daemon-reload
systemctl enable --quiet tracker
# restart, not `enable --now`: that leaves an already-running service alone,
# so on a re-run the process would keep the .env and the code it started with
# and every update would silently have no effect.
systemctl restart tracker
sleep 2

if systemctl is-active --quiet tracker; then
  info "tracker is running"
else
  journalctl -u tracker -n 30 --no-pager || true
  die "tracker does not start — log above."
fi

# --- nginx ----------------------------------------------------------------
bold ""
bold "Web server"
sed -e "s/tracker\.example\.de/$DOMAIN/g" \
    -e "s|127\.0\.0\.1:8000|127.0.0.1:$PORT|g" \
    deploy/nginx.conf > /etc/nginx/sites-available/tracker
ln -sf /etc/nginx/sites-available/tracker /etc/nginx/sites-enabled/tracker

# Only drop the stock placeholder site, never a config someone else put here.
if [[ -L /etc/nginx/sites-enabled/default ]] && \
   [[ "$(readlink -f /etc/nginx/sites-enabled/default)" == /etc/nginx/sites-available/default ]] && \
   ! grep -q 'proxy_pass' /etc/nginx/sites-available/default 2>/dev/null; then
  rm -f /etc/nginx/sites-enabled/default
  info "nginx default site disabled"
fi

nginx -t >/dev/null 2>&1 || { nginx -t; die "nginx configuration is invalid."; }
systemctl reload nginx
info "nginx configured"

# --- TLS ------------------------------------------------------------------
bold ""
bold "Certificate"
if certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos \
     --email "$EMAIL" --redirect >/dev/null 2>&1; then
  info "Certificate issued, HTTPS active"
  TLS_OK=1
else
  warn "certbot failed — usually the DNS record does not point here yet."
  warn "Check the A record, then retry:  certbot --nginx -d $DOMAIN --redirect"
  TLS_OK=0
fi

# --------------------------------------------------------------------------
SCHEME=$([[ ${TLS_OK:-0} -eq 1 ]] && echo https || echo http)

bold ""
bold "Done."
bold ""
info "App:   $SCHEME://$DOMAIN"
bold ""
bold "  Open this link on your phone — it fills in token and wallet for you:"
bold ""
SETUP_LINK="$SCHEME://$DOMAIN/#/setup?token=$ACCESS_TOKEN"
[[ -n $WALLET ]] && SETUP_LINK="$SETUP_LINK&wallet=$WALLET"
printf '  \033[1;33m%s\033[0m\n' "$SETUP_LINK"
bold ""
info "Then: Share → Add to Home Screen."
if [[ ${TLS_OK:-0} -ne 1 ]]; then
  warn "Without HTTPS the app cannot be installed and will not work offline."
fi
bold ""
