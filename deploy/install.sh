#!/usr/bin/env bash
# install.sh — one-command production installer for Invoice Maid.
#
# What it does (idempotent; re-run is safe):
#   1. Create a dedicated system user ({{USER}})
#   2. Create /etc/invoice-maid/ (0750 root:{{USER}}) and a data dir under
#      /var/lib/{{USER}}/ owned by the service user
#   3. Clone the repo at the latest tag (or --tag X / --branch Y) into
#      {{HOME}}/src
#   4. Create a Python venv at {{HOME}}/venv and pip install backend[full]
#   5. Generate JWT_SECRET (32 bytes, hex) and bcrypt-hash the admin password
#      (prompted via `read -s` so it never lands in shell history)
#   6. Write /etc/invoice-maid/invoice-maid.env with absolute paths for
#      DATABASE_URL / STORAGE_PATH so data lives outside the checkout
#   7. Symlink {{HOME}}/src/backend/.env -> /etc/invoice-maid/invoice-maid.env
#   8. Run `alembic upgrade head`
#   9. Render templates from deploy/ (invoice-maid.service,
#      invoice-maid.service) and install it
#  10. Install deploy/invoice-maid-upgrade to /usr/local/sbin/
#  11. `systemctl daemon-reload` + enable and start the service
#
# What it does NOT do:
#   - TLS / reverse proxy (see deploy/README.md for nginx + cert examples)
#   - Prompt for LLM_API_KEY (left as REPLACE_ME with a banner at the end)
#   - Open firewall ports
#
# Usage:
#   sudo ./install.sh                     # interactive
#   sudo ./install.sh --yes --admin-password-stdin < password.txt
#   sudo ./install.sh --yes --random-admin-password
#   sudo ./install.sh --dry-run           # preview, change nothing
#   sudo ./install.sh --tag v0.3.0        # pin to specific version
#   sudo ./install.sh --branch main       # bleeding edge
#
# Flags (all optional; prompted when interactive):
#   --yes                         Non-interactive; use defaults + flags
#   --user NAME                   Service user (default: invoice-maid)
#   --home DIR                    Installation home (default: /var/lib/<user>)
#   --port N                      Bind port on 127.0.0.1 (default: 8000)
#   --tag VERSION                 Check out this tag (default: latest v*)
#   --branch BRANCH               Check out this branch instead of a tag
#   --repo URL                    Git remote URL (default: official)
#   --admin-password-stdin        Read admin password from stdin
#   --random-admin-password       Generate + print random password once
#   --llm-base-url URL            Default: https://api.openai.com/v1
#   --llm-api-key KEY             If omitted, REPLACE_ME placeholder is used
#   --dry-run                     Print actions without executing
#   --skip-start                  Install but do not enable or start service
#   --upgrade-only                Re-run only steps 3, 4, 8, 10 (for updates)
#
# Exit codes:
#   0 success     1 usage error     2 precondition failure
#   3 clone/pip   4 migration       5 systemd failure

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR

DEFAULT_USER="invoice-maid"
DEFAULT_PORT=8000
DEFAULT_REPO="https://github.com/helixzz/invoice-maid.git"
DEFAULT_LLM_BASE_URL="https://api.openai.com/v1"

USER_NAME="$DEFAULT_USER"
HOME_DIR=""
PORT="$DEFAULT_PORT"
REF_MODE=tag
REF=""
REPO_URL="$DEFAULT_REPO"
ADMIN_MODE=interactive
ADMIN_PASSWORD=""
LLM_BASE_URL="$DEFAULT_LLM_BASE_URL"
LLM_API_KEY=""
YES=0
DRY=0
SKIP_START=0
UPGRADE_ONLY=0

die() { echo "error: $*" >&2; exit 1; }
usage() { sed -n '2,55p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

parse_args() {
  while (($#)); do
    case "$1" in
      --yes) YES=1; shift ;;
      --user) USER_NAME="${2:?}"; shift 2 ;;
      --home) HOME_DIR="${2:?}"; shift 2 ;;
      --port) PORT="${2:?}"; shift 2 ;;
      --tag) REF_MODE=tag; REF="${2:?}"; shift 2 ;;
      --branch) REF_MODE=branch; REF="${2:?}"; shift 2 ;;
      --repo) REPO_URL="${2:?}"; shift 2 ;;
      --admin-password-stdin) ADMIN_MODE=stdin; shift ;;
      --random-admin-password) ADMIN_MODE=random; shift ;;
      --llm-base-url) LLM_BASE_URL="${2:?}"; shift 2 ;;
      --llm-api-key) LLM_API_KEY="${2:?}"; shift 2 ;;
      --dry-run) DRY=1; shift ;;
      --skip-start) SKIP_START=1; shift ;;
      --upgrade-only) UPGRADE_ONLY=1; shift ;;
      -h|--help) usage ;;
      *) die "unknown argument: $1" ;;
    esac
  done
  HOME_DIR="${HOME_DIR:-/var/lib/${USER_NAME}}"
}

ts()  { date +'%F %T'; }
log() { printf '[%s] %s\n' "$(ts)" "$*"; }
run() { log "+ $*"; (( DRY )) && return 0; "$@"; }
# CRITICAL: installer often runs from the caller's interactive CWD (e.g. a
# home directory with mode 750). pip's editable install puts a sentinel on
# sys.path that it later os.stat()s as a RELATIVE path — if CWD is not
# traversable by the service user, that stat returns EACCES and pip aborts.
# run_as() forces CWD to a path the service user definitely owns.
run_as() {
  local cwd="$1"; shift
  log "+ [$USER_NAME in $cwd] $*"
  (( DRY )) && return 0
  sudo -u "$USER_NAME" env -C "$cwd" "$@"
}

require_root() {
  [[ $EUID -eq 0 ]] || die "must run as root (use sudo)"
}

require_commands() {
  local missing=()
  for cmd in "$@"; do
    command -v "$cmd" >/dev/null || missing+=("$cmd")
  done
  (( ${#missing[@]} == 0 )) || die "missing commands: ${missing[*]}"
}

detect_python() {
  for py in python3.13 python3.12 python3.11; do
    if command -v "$py" >/dev/null; then
      PYTHON_BIN="$py"
      return
    fi
  done
  die "no supported Python found (need 3.11+)"
}

confirm_or_die() {
  (( YES )) && return 0
  local reply
  read -r -p "$1 [y/N] " reply
  [[ "$reply" =~ ^[Yy]$ ]] || die "aborted by user"
}

ensure_user() {
  if id "$USER_NAME" >/dev/null 2>&1; then
    log "user '$USER_NAME' already exists"
  else
    run useradd --system --home-dir "$HOME_DIR" --create-home \
                --shell /usr/sbin/nologin "$USER_NAME"
  fi
}

ensure_dirs() {
  run install -d -m 0750 -o root -g "$USER_NAME" /etc/invoice-maid
  run install -d -m 0750 -o "$USER_NAME" -g "$USER_NAME" "$HOME_DIR/data"
  run install -d -m 0750 -o "$USER_NAME" -g "$USER_NAME" "$HOME_DIR/data/invoices"
}

clone_or_fetch() {
  local src="$HOME_DIR/src"
  if [[ -d "$src/.git" ]]; then
    log "repo already cloned; fetching"
    run sudo -u "$USER_NAME" git -C "$src" fetch --tags --prune --force origin
  else
    run sudo -u "$USER_NAME" git clone "$REPO_URL" "$src"
  fi

  local target
  case "$REF_MODE" in
    tag)
      if [[ -z "$REF" ]]; then
        target=$(sudo -u "$USER_NAME" git -C "$src" tag --sort=-v:refname | grep -E '^v[0-9]+\.' | head -n1 || true)
        [[ -n "$target" ]] || die "no v* tag in repo"
      else
        target="$REF"
      fi
      ;;
    branch) target="origin/$REF" ;;
  esac
  log "checkout target: $target"
  run sudo -u "$USER_NAME" git -C "$src" checkout -f "$target"
}

ensure_venv_and_install() {
  local venv="$HOME_DIR/venv"
  local src="$HOME_DIR/src"
  if [[ ! -x "$venv/bin/python" ]]; then
    run_as "$src" "$PYTHON_BIN" -m venv "$venv"
    run_as "$src" "$venv/bin/pip" install --upgrade pip wheel
  fi
  run_as "$src" "$venv/bin/pip" install --upgrade \
      -e "${src}/backend[full]"
}

read_admin_password() {
  case "$ADMIN_MODE" in
    stdin)
      ADMIN_PASSWORD=$(cat)
      [[ -n "$ADMIN_PASSWORD" ]] || die "--admin-password-stdin: empty input"
      ;;
    random)
      ADMIN_PASSWORD=$(tr -dc 'A-Za-z0-9' </dev/urandom | head -c 20 || true)
      echo ">>> generated admin password: $ADMIN_PASSWORD"
      echo ">>> save this NOW; it will not be shown again"
      ;;
    interactive)
      (( YES )) && die "non-interactive mode needs --admin-password-stdin or --random-admin-password"
      local p1 p2
      while :; do
        read -r -s -p "Admin password (min 8 chars): " p1; echo
        [[ "${#p1}" -ge 8 ]] || { echo "too short"; continue; }
        read -r -s -p "Confirm password: " p2; echo
        [[ "$p1" == "$p2" ]] && break
        echo "mismatch; try again"
      done
      ADMIN_PASSWORD="$p1"
      ;;
  esac
}

# Hash the password INSIDE the venv's Python, reading plaintext from stdin so
# it never appears in argv or process listings.
hash_admin_password() {
  local venv="$HOME_DIR/venv"
  local hash
  hash=$(printf '%s' "$ADMIN_PASSWORD" | sudo -u "$USER_NAME" env -C "$HOME_DIR" "$venv/bin/python" \
         -c 'import sys; from passlib.hash import bcrypt; print(bcrypt.hash(sys.stdin.read()))')
  [[ "$hash" =~ ^\$2[aby]\$ ]] || die "bcrypt hash looks wrong: $hash"
  ADMIN_HASH="$hash"
  unset ADMIN_PASSWORD
}

write_env_file() {
  local env_file=/etc/invoice-maid/invoice-maid.env
  local data_dir="$HOME_DIR/data"
  local jwt_secret
  jwt_secret=$(openssl rand -hex 32)

  if [[ -f "$env_file" ]]; then
    log "$env_file already exists; leaving it untouched"
    log "  if you want to regenerate, back it up and remove it, then re-run"
    return 0
  fi

  local key_line
  if [[ -n "$LLM_API_KEY" ]]; then
    key_line="LLM_API_KEY=${LLM_API_KEY}"
  else
    key_line="LLM_API_KEY=REPLACE_ME_WITH_REAL_KEY"
  fi

  run bash -c "cat >'$env_file' <<EOF
# /etc/invoice-maid/invoice-maid.env
# Managed by install.sh; reloaded on \`systemctl restart invoice-maid\`.

# -------- REQUIRED --------
DATABASE_URL=sqlite+aiosqlite:////${data_dir#/}/invoices.db
STORAGE_PATH=${data_dir}/invoices
ADMIN_PASSWORD_HASH=${ADMIN_HASH}
JWT_SECRET=${jwt_secret}

# -------- LLM --------
LLM_BASE_URL=${LLM_BASE_URL}
${key_line}
LLM_MODEL=gpt-4o-mini
LLM_EMBED_MODEL=text-embedding-3-small
EMBED_DIM=1536

# -------- TUNING --------
JWT_EXPIRE_MINUTES=1440
SCAN_INTERVAL_MINUTES=60
SQLITE_VEC_ENABLED=true
LOG_LEVEL=INFO
EOF"
  run chown root:"$USER_NAME" "$env_file"
  run chmod 0640 "$env_file"
}

link_env_into_checkout() {
  local dest="$HOME_DIR/src/backend/.env"
  local target=/etc/invoice-maid/invoice-maid.env
  if [[ -L "$dest" ]] && [[ "$(readlink "$dest")" == "$target" ]]; then
    log "$dest already links to $target"
    return 0
  fi
  run sudo -u "$USER_NAME" ln -sf "$target" "$dest"
}

run_alembic() {
  run sudo -u "$USER_NAME" env -C "$HOME_DIR/src/backend" \
      "$HOME_DIR/venv/bin/alembic" upgrade head
}

install_systemd_units() {
  local service=/etc/systemd/system/invoice-maid.service

  render_service > /tmp/invoice-maid.service
  run install -m 0644 -o root -g root /tmp/invoice-maid.service "$service"
  run rm -f /tmp/invoice-maid.service

  run systemctl daemon-reload
}

render_service() {
  sed -e "s|{{USER}}|${USER_NAME}|g" \
      -e "s|{{INSTALL_DIR}}|${HOME_DIR}/src|g" \
      -e "s|{{VENV}}|${HOME_DIR}/venv|g" \
      -e "s|{{ENV_FILE}}|/etc/invoice-maid/invoice-maid.env|g" \
      -e "s|{{PORT}}|${PORT}|g" \
      -e "s|{{DATA_DIR}}|${HOME_DIR}|g" \
      "$SCRIPT_DIR/invoice-maid.service"
}

install_cli_tools() {
  run install -m 0755 -o root -g root \
      "$SCRIPT_DIR/invoice-maid-upgrade" /usr/local/sbin/invoice-maid-upgrade

  if [[ ! -f /etc/default/invoice-maid ]]; then
    run bash -c "cat >/etc/default/invoice-maid <<EOF
# Read by invoice-maid-upgrade.
# Defaults match the install.sh layout; edit to taste.
INVOICE_MAID_USER=${USER_NAME}
INVOICE_MAID_HOME=${HOME_DIR}
INVOICE_MAID_SRC=${HOME_DIR}/src
INVOICE_MAID_VENV=${HOME_DIR}/venv
INVOICE_MAID_ENV_FILE=/etc/invoice-maid/invoice-maid.env
INVOICE_MAID_SERVICE=invoice-maid
INVOICE_MAID_HEALTH_URL=http://127.0.0.1:${PORT}/api/v1/health
INVOICE_MAID_DATA=${HOME_DIR}/data
EOF"
    run chmod 0644 /etc/default/invoice-maid
  fi
}

enable_and_start() {
  if (( SKIP_START )); then
    log "--skip-start: leaving service disabled; start it yourself with:"
    log "  sudo systemctl enable --now invoice-maid"
    return 0
  fi
  run systemctl enable --now invoice-maid
}

final_banner() {
  cat <<BANNER

================================================================================
  Invoice Maid install complete
================================================================================
  User         : $USER_NAME
  Home         : $HOME_DIR
  Port         : 127.0.0.1:$PORT
  Env file     : /etc/invoice-maid/invoice-maid.env
  Data dir     : $HOME_DIR/data
  Service      : systemctl status invoice-maid
  Upgrade      : sudo invoice-maid-upgrade
  Logs         : sudo journalctl -u invoice-maid -f

NEXT STEPS
  1. Set LLM_API_KEY in /etc/invoice-maid/invoice-maid.env, then:
        sudo systemctl restart invoice-maid
  2. Configure a reverse proxy with TLS (see deploy/README.md)
  3. Browse to http://127.0.0.1:$PORT and log in as 'admin'
================================================================================
BANNER
}

main() {
  require_root
  parse_args "$@"

  if (( UPGRADE_ONLY )); then
    require_commands git systemctl sudo
    id "$USER_NAME" >/dev/null 2>&1 || die "user '$USER_NAME' not found"
    detect_python
    clone_or_fetch
    ensure_venv_and_install
    run_alembic
    log "upgrade-only complete"
    return 0
  fi

  require_commands git systemctl sudo openssl curl
  detect_python

  log "installing Invoice Maid:"
  log "  user=$USER_NAME home=$HOME_DIR port=$PORT ref=$REF_MODE:${REF:-<latest>}"
  confirm_or_die "Proceed?"

  ensure_user
  ensure_dirs
  clone_or_fetch
  ensure_venv_and_install
  read_admin_password
  hash_admin_password
  write_env_file
  link_env_into_checkout
  run_alembic
  install_systemd_units
  install_cli_tools
  enable_and_start
  final_banner
}

main "$@"
