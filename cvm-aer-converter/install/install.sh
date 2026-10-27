#!/usr/bin/env bash
# Installs - or, run again, updates - the CVM-AER converter on a Linux host, straight
# from GitHub: only the cvm-aer-converter folder is checked out.
#
#   sudo ./install.sh --branch main --service
#
# Layout (defaults):
#   /opt/cvm-aer/src/cvm-aer-converter   the code (replaced on every run)
#   /opt/cvm-aer/.venv                   Python environment with requirements.txt
#   /opt/cvm-aer/cvm-aer-service.ini     the configuration (written once, never overwritten)
#   /opt/cvm-aer/run-cvm-aer             runs "python -m cvm_aer" with that ini
#   /srv/cvm-aer/{inbound,outbound,processed,error}
#
# The repository is private: export GITHUB_TOKEN=<token with read access to it> first.
# The token is used for the download only and is not saved anywhere.
set -euo pipefail

REPO_URL="${CVM_AER_REPO:-https://github.com/asaxena431/pharmawatch-dashboard.git}"
BRANCH="main"
APP_DIR="/opt/cvm-aer"
DATA_DIR="/srv/cvm-aer"
PYTHON="python3"
WITH_OCR=0
SERVICE=0
SERVICE_USER="${SUDO_USER:-$(id -un)}"
SERVICE_NAME="cvm-aer"
CODE_FOLDER="cvm-aer-converter"

usage() {
    cat <<USAGE
Usage: $0 [options]
  --branch NAME        GitHub branch to install (default: $BRANCH)
  --repo URL           repository URL or local path (default: $REPO_URL)
  --app-dir DIR        code, Python environment and ini (default: $APP_DIR)
  --data-dir DIR       inbound/outbound/processed/error (default: $DATA_DIR)
  --python EXE         Python 3.10 or newer (default: $PYTHON)
  --with-ocr           also install PaddleOCR (scanned forms only; about 1 GB)
  --service            install and start the systemd service $SERVICE_NAME (needs root)
  --service-user USER  account the service runs as (default: $SERVICE_USER)
USAGE
}

while [ $# -gt 0 ]; do
    case "$1" in
        --branch) BRANCH="$2"; shift 2 ;;
        --repo) REPO_URL="$2"; shift 2 ;;
        --app-dir) APP_DIR="$2"; shift 2 ;;
        --data-dir) DATA_DIR="$2"; shift 2 ;;
        --python) PYTHON="$2"; shift 2 ;;
        --with-ocr) WITH_OCR=1; shift ;;
        --service) SERVICE=1; shift ;;
        --service-user) SERVICE_USER="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

step() { printf '\n==> %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

step "Checking prerequisites"
command -v git >/dev/null || die "git is not installed (e.g. sudo dnf install git / sudo apt install git)"
command -v "$PYTHON" >/dev/null || die "$PYTHON is not installed"
"$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 10))' \
    || die "$PYTHON is $("$PYTHON" -V 2>&1); Python 3.10 or newer is needed"
"$PYTHON" -c 'import venv, ensurepip' 2>/dev/null \
    || die "$PYTHON has no venv/ensurepip module (e.g. sudo apt install python3-venv)"
if [ "$SERVICE" = 1 ]; then
    [ "$(id -u)" = 0 ] || die "--service needs root: run with sudo"
    id "$SERVICE_USER" >/dev/null 2>&1 || die "no such user: $SERVICE_USER"
fi
echo "git $(git --version | awk '{print $3}'), $("$PYTHON" -V 2>&1)"

GIT_AUTH=()
if [ -n "${GITHUB_TOKEN:-}" ]; then
    GIT_AUTH=(-c "http.extraHeader=Authorization: Basic $(printf 'x-access-token:%s' "$GITHUB_TOKEN" | base64 | tr -d '\n')")
fi
git_() { git ${GIT_AUTH[@]+"${GIT_AUTH[@]}"} "$@"; }

SRC="$APP_DIR/src"
CODE="$SRC/$CODE_FOLDER"
VENV="$APP_DIR/.venv"
INI="$APP_DIR/cvm-aer-service.ini"
RUN="$APP_DIR/run-cvm-aer"

step "Getting $CODE_FOLDER ($BRANCH) from $REPO_URL"
mkdir -p "$APP_DIR"
if [ -d "$SRC/.git" ]; then
    git -C "$SRC" remote set-url origin "$REPO_URL"
    git_ -C "$SRC" fetch --depth 1 origin "$BRANCH"
    git -C "$SRC" checkout --force -B "$BRANCH" FETCH_HEAD
else
    rm -rf "$SRC"
    git_ clone --depth 1 --branch "$BRANCH" --no-checkout "$REPO_URL" "$SRC"
    git -C "$SRC" sparse-checkout set "$CODE_FOLDER"
    git -C "$SRC" checkout --force "$BRANCH"
fi
[ -f "$CODE/requirements.txt" ] || die "branch $BRANCH has no $CODE_FOLDER folder"
COMMIT="$(git -C "$SRC" rev-parse --short HEAD)"
echo "installed commit $COMMIT"

step "Python environment $VENV"
[ -x "$VENV/bin/python" ] || "$PYTHON" -m venv "$VENV"
"$VENV/bin/python" -m pip install --quiet --upgrade pip
"$VENV/bin/python" -m pip install --quiet -r "$CODE/requirements.txt"
if [ "$WITH_OCR" = 1 ]; then
    "$VENV/bin/python" -m pip install --quiet -r "$CODE/requirements-ocr.txt"
fi

step "Folders under $DATA_DIR"
for folder in inbound outbound processed error; do
    mkdir -p "$DATA_DIR/$folder"
    echo "  $DATA_DIR/$folder"
done

step "Configuration $INI"
if [ -f "$INI" ]; then
    echo "kept the existing file (compare it with $CODE/cvm-aer-service.ini.sample for new settings)"
else
    sed -e "s|^inbound = .*|inbound = $DATA_DIR/inbound|" \
        -e "s|^outbound = .*|outbound = $DATA_DIR/outbound|" \
        -e "s|^processed = .*|processed = $DATA_DIR/processed|" \
        -e "s|^error = .*|error = $DATA_DIR/error|" \
        "$CODE/cvm-aer-service.ini.sample" > "$INI"
    echo "written from the sample - fill in [intake] and [email] before starting the service"
fi

cat > "$RUN" <<RUNNER
#!/usr/bin/env bash
# Written by install.sh: python -m cvm_aer with this installation's code, packages and ini.
export CVM_AER_CONFIG="\${CVM_AER_CONFIG:-$INI}"
export PYTHONPATH="$CODE"
exec "$VENV/bin/python" -m cvm_aer "\$@"
RUNNER
chmod 755 "$RUN"

step "Self-check: fill the sample Form FDA 1932, convert it and validate the message"
CHECK="$(mktemp -d)"
trap 'rm -rf "$CHECK"' EXIT
"$RUN" samples --output-dir "$CHECK" >/dev/null
SAMPLE="$(find "$CHECK" -name '*.pdf' | head -n 1)"
[ -n "$SAMPLE" ] || die "the sample form was not created"
"$RUN" convert "$SAMPLE" --quiet --output "$CHECK/sample.xml"
"$RUN" validate "$CHECK/sample.xml"

if [ "$SERVICE" = 1 ]; then
    step "systemd service $SERVICE_NAME (runs as $SERVICE_USER)"
    chown -R "$SERVICE_USER": "$DATA_DIR"
    chown "$SERVICE_USER": "$INI"
    chmod 640 "$INI"
    cat > "/etc/systemd/system/$SERVICE_NAME.service" <<UNIT
[Unit]
Description=CVM-AER converter: Form FDA 1932/1932a case e-mails and ZIPs -> validated VICH HL7 XML
Wants=network-online.target
After=network-online.target remote-fs.target

[Service]
User=$SERVICE_USER
WorkingDirectory=$APP_DIR
Environment=CVM_AER_CONFIG=$INI
Environment=PYTHONPATH=$CODE
ExecStart=$VENV/bin/python -m cvm_aer
Restart=on-failure
RestartSec=30

[Install]
WantedBy=multi-user.target
UNIT
    systemctl daemon-reload
    systemctl enable "$SERVICE_NAME" >/dev/null
    systemctl restart "$SERVICE_NAME"
    systemctl --no-pager --lines=0 status "$SERVICE_NAME" || true
fi

step "Done: commit $COMMIT is installed in $APP_DIR"
cat <<DONE
  edit the settings:  $INI
  one sweep:          $RUN --once
  read e-mails only:  $RUN intake
  convert one form:   $RUN convert case.pdf -o case.xml
DONE
if [ "$SERVICE" = 1 ]; then
    echo "  service log:        journalctl -u $SERVICE_NAME -f"
else
    echo "  run continuously:   $RUN     (or re-run this script with sudo ... --service)"
fi
