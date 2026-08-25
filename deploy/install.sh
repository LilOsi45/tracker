#!/usr/bin/env bash
#
# One-shot installer for a fresh Debian/Ubuntu box.
#
# Written to be driven from a phone over SSH: it asks three short questions,
# generates the access token itself, and prints a tap-to-configure link at
# the end so nothing long has to be typed on a touch keyboard.
#
#   ssh root@<server-ip>
#   curl -fsSL https://raw.githubusercontent.com/LilOsi45/tracker/claude/solana-memecoin-trading-igtdv3/deploy/install.sh | bash
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

[[ $EUID -eq 0 ]] || die "Bitte als root ausführen (sudo -i)."
command -v apt-get >/dev/null || die "Erwartet Debian oder Ubuntu."

# The pinned dependencies ship wheels up to cp313. On a newer interpreter pip
# falls back to building from source, which needs Rust for pydantic-core and a
# C toolchain for uvloop/httptools/watchfiles — neither of which this script
# installs. Better to stop here than to leave a half-built venv behind.
if command -v python3 >/dev/null; then
  PY_MINOR="$(python3 -c 'import sys; print(sys.version_info[1])')"
  if (( PY_MINOR > 13 )); then
    die "Python 3.$PY_MINOR gefunden, unterstützt sind 3.9 bis 3.13.
     Bitte den Server mit Ubuntu 24.04 LTS neu aufsetzen (bringt Python 3.12 mit)."
  fi
  if (( PY_MINOR < 9 )); then
    die "Python 3.$PY_MINOR ist zu alt. Ubuntu 24.04 LTS verwenden."
  fi
fi

# Prompts must read from the terminal, not from the piped script body.
if [[ -t 0 ]]; then TTY=/dev/stdin; else TTY=/dev/tty; fi
[[ -r $TTY ]] || die "Keine Eingabe möglich. Skript herunterladen und direkt ausführen."

ask() {
  local prompt="$1" default="${2:-}" answer
  if [[ -n $default ]]; then
    read -r -p "  $prompt [$default]: " answer <"$TTY" || true
    printf '%s' "${answer:-$default}"
  else
    read -r -p "  $prompt: " answer <"$TTY" || true
    printf '%s' "$answer"
  fi
}

# --------------------------------------------------------------------------
bold ""
bold "Tracker — Installation"
bold ""

DOMAIN="$(ask 'Domain (z.B. tracker.deine-domain.de)')"
[[ -n $DOMAIN ]] || die "Ohne Domain kein Zertifikat und keine installierbare App."

HELIUS_KEY="$(ask 'Helius API-Key (leer lassen geht, dann kein Wallet-Sync)')"
WALLET="$(ask 'Solana-Wallet-Adresse (optional)')"
EMAIL="$(ask "E-Mail für Let's-Encrypt-Ablaufwarnungen" "admin@${DOMAIN#*.}")"

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
    raise SystemExit("kein freier Port zwischen 8000 und 8099")
PY
}

PORT=""
if [[ -f /etc/systemd/system/tracker.service ]]; then
  PORT="$(grep -oP -- '(?<=--port )\d+' /etc/systemd/system/tracker.service || true)"
fi

bold ""
info "Domain:  $DOMAIN"
info "Helius:  $([[ -n $HELIUS_KEY ]] && echo 'gesetzt' || echo 'fehlt — Coin-Übersicht läuft, Wallet-Sync nicht')"
info "Wallet:  ${WALLET:-nicht gesetzt}"
bold ""

# --- DNS sanity check -----------------------------------------------------
# certbot will fail with a confusing error if the record is missing or still
# points somewhere else, so check before spending the attempt.
PUBLIC_IP="$(curl -fsS --max-time 10 https://api.ipify.org 2>/dev/null || true)"
RESOLVED="$(getent ahostsv4 "$DOMAIN" 2>/dev/null | awk 'NR==1{print $1}' || true)"

if [[ -z $RESOLVED ]]; then
  warn "$DOMAIN löst nicht auf. A-Record anlegen, dann dieses Skript erneut starten."
  warn "Bis dahin bricht die Zertifikatsausstellung ab."
elif [[ -n $PUBLIC_IP && $RESOLVED != "$PUBLIC_IP" ]]; then
  warn "$DOMAIN zeigt auf $RESOLVED, dieser Server ist $PUBLIC_IP."
  warn "Solange das so ist, schlägt certbot fehl."
else
  info "DNS zeigt korrekt hierher ($RESOLVED)."
fi

# --- packages -------------------------------------------------------------
bold ""
bold "Pakete"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git python3 python3-venv python3-pip nginx certbot \
  python3-certbot-nginx curl ca-certificates iproute2 openssl >/dev/null
info "installiert"

# ss is available now, so a free port can be chosen.
if [[ -n $PORT ]]; then
  info "Port $PORT aus bestehender Installation übernommen"
else
  PORT="$(pick_port)"
  [[ $PORT == 8000 ]] && info "Port $PORT" \
    || warn "Port 8000 ist belegt, Tracker läuft auf $PORT"
fi

# --- user and checkout ----------------------------------------------------
bold ""
bold "Anwendung"

id -u "$APP_USER" >/dev/null 2>&1 || \
  useradd --system --no-create-home --shell /usr/sbin/nologin "$APP_USER"

if [[ -d $APP_DIR/.git ]]; then
  git -C "$APP_DIR" fetch --quiet origin "$REPO_BRANCH"
  git -C "$APP_DIR" checkout --quiet "$REPO_BRANCH"
  git -C "$APP_DIR" reset --hard --quiet "origin/$REPO_BRANCH"
  info "Checkout aktualisiert"
else
  git clone --quiet --branch "$REPO_BRANCH" "$REPO_URL" "$APP_DIR"
  info "Repository geklont"
fi

cd "$APP_DIR"
python3 -m venv backend/.venv
backend/.venv/bin/pip install --quiet --upgrade pip
backend/.venv/bin/pip install --quiet -r backend/requirements.txt
info "Abhängigkeiten installiert"

install -o "$APP_USER" -g "$APP_USER" -d "$APP_DIR/data"

# --- configuration --------------------------------------------------------
# An existing token is reused so a re-run does not lock the phone out.
if [[ -f .env ]] && grep -q '^ACCESS_TOKEN=.\+' .env; then
  ACCESS_TOKEN="$(grep '^ACCESS_TOKEN=' .env | cut -d= -f2-)"
  info "Bestehender Access-Token übernommen"
else
  ACCESS_TOKEN="$(openssl rand -hex 24)"
  info "Access-Token erzeugt"
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
  info "config.yaml angelegt"
else
  info "config.yaml existiert, unverändert gelassen"
fi
chown "$APP_USER:$APP_USER" config.yaml

# --- service --------------------------------------------------------------
bold ""
bold "Dienst"
sed "s/--port 8000/--port $PORT/" deploy/tracker.service > /etc/systemd/system/tracker.service
systemctl daemon-reload
systemctl enable --quiet --now tracker
sleep 2

if systemctl is-active --quiet tracker; then
  info "tracker läuft"
else
  journalctl -u tracker -n 30 --no-pager || true
  die "tracker startet nicht — Log oben."
fi

# --- nginx ----------------------------------------------------------------
bold ""
bold "Webserver"
sed -e "s/tracker\.example\.de/$DOMAIN/g" \
    -e "s|127\.0\.0\.1:8000|127.0.0.1:$PORT|g" \
    deploy/nginx.conf > /etc/nginx/sites-available/tracker
ln -sf /etc/nginx/sites-available/tracker /etc/nginx/sites-enabled/tracker

# Only drop the stock placeholder site, never a config someone else put here.
if [[ -L /etc/nginx/sites-enabled/default ]] && \
   [[ "$(readlink -f /etc/nginx/sites-enabled/default)" == /etc/nginx/sites-available/default ]] && \
   ! grep -q 'proxy_pass' /etc/nginx/sites-available/default 2>/dev/null; then
  rm -f /etc/nginx/sites-enabled/default
  info "nginx-Standardseite deaktiviert"
fi

nginx -t >/dev/null 2>&1 || { nginx -t; die "nginx-Konfiguration fehlerhaft."; }
systemctl reload nginx
info "nginx konfiguriert"

# --- TLS ------------------------------------------------------------------
bold ""
bold "Zertifikat"
if certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos \
     --email "$EMAIL" --redirect >/dev/null 2>&1; then
  info "Zertifikat ausgestellt, HTTPS aktiv"
  TLS_OK=1
else
  warn "certbot fehlgeschlagen — meist zeigt der DNS-Eintrag noch nicht hierher."
  warn "A-Record prüfen, dann erneut:  certbot --nginx -d $DOMAIN --redirect"
  TLS_OK=0
fi

# --------------------------------------------------------------------------
SCHEME=$([[ ${TLS_OK:-0} -eq 1 ]] && echo https || echo http)

bold ""
bold "Fertig."
bold ""
info "App:   $SCHEME://$DOMAIN"
bold ""
bold "  Diesen Link auf dem Handy öffnen — er trägt Token und Wallet selbst ein:"
bold ""
SETUP_LINK="$SCHEME://$DOMAIN/#/setup?token=$ACCESS_TOKEN"
[[ -n $WALLET ]] && SETUP_LINK="$SETUP_LINK&wallet=$WALLET"
printf '  \033[1;33m%s\033[0m\n' "$SETUP_LINK"
bold ""
info "Danach: Teilen → Zum Home-Bildschirm."
if [[ ${TLS_OK:-0} -ne 1 ]]; then
  warn "Ohne HTTPS ist die App nicht installierbar und nicht offline nutzbar."
fi
bold ""
