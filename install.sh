#!/usr/bin/env bash
#
# install.sh - GenBI Platform installer
#
# Design principle: this script does the MINIMUM possible on the bare host --
# install Docker Engine + Compose plugin, generate secrets, and build/start
# the compose stack defined in ./docker-compose.yml at the repo root. Every
# other dependency (Python version, ODBC/FreeTDS drivers, Node build
# tooling) lives inside the Dockerfiles under backend/ and frontend-*/, so
# this script's job stays small, idempotent, and safe to re-run.
#
# Usage (from the root of a cloned copy of this repository):
#   sudo ./install.sh
#
# Safe to re-run: every step checks current state before acting. Re-running
# after a partial failure will not duplicate secrets, users, or volumes,
# and will never overwrite an existing .env file.

set -Eeuo pipefail
IFS=$'\n\t'

# ---------------------------------------------------------------------------
# Configuration constants
# ---------------------------------------------------------------------------
readonly REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly GENBI_ENV_FILE="${REPO_ROOT}/.env"
readonly GENBI_COMPOSE_FILE="${REPO_ROOT}/docker-compose.yml"
readonly GENBI_LOG_FILE="/var/log/genbi-install.log"
readonly REQUIRED_UBUNTU_CODENAMES=("focal" "jammy" "noble")

# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------
log_info()  { echo -e "[INFO]  $(date '+%Y-%m-%d %H:%M:%S') - $*" | tee -a "${GENBI_LOG_FILE}"; }
log_warn()  { echo -e "[WARN]  $(date '+%Y-%m-%d %H:%M:%S') - $*" | tee -a "${GENBI_LOG_FILE}" >&2; }
log_error() { echo -e "[ERROR] $(date '+%Y-%m-%d %H:%M:%S') - $*" | tee -a "${GENBI_LOG_FILE}" >&2; }

fail_with_message() {
    log_error "$1"
    log_error "Installation aborted. See ${GENBI_LOG_FILE} for full details."
    exit 1
}

trap 'log_error "Unexpected failure at line ${LINENO} while running: ${BASH_COMMAND}"' ERR

# ---------------------------------------------------------------------------
# Step 0: Preconditions
# ---------------------------------------------------------------------------
require_root() {
    if [[ "${EUID}" -ne 0 ]]; then
        fail_with_message "This script must be run as root (try: sudo ./install.sh)."
    fi
}

require_supported_ubuntu() {
    if [[ ! -f /etc/os-release ]]; then
        fail_with_message "Cannot detect operating system (/etc/os-release not found). This installer targets Ubuntu LTS only."
    fi

    # shellcheck disable=SC1091
    source /etc/os-release

    if [[ "${ID:-}" != "ubuntu" ]]; then
        fail_with_message "Detected OS '${ID:-unknown}'. This installer only supports Ubuntu LTS."
    fi

    local codename_supported=false
    for supported in "${REQUIRED_UBUNTU_CODENAMES[@]}"; do
        if [[ "${VERSION_CODENAME:-}" == "${supported}" ]]; then
            codename_supported=true
            break
        fi
    done

    if [[ "${codename_supported}" != "true" ]]; then
        log_warn "Ubuntu codename '${VERSION_CODENAME:-unknown}' is not in the explicitly tested list " \
                 "(${REQUIRED_UBUNTU_CODENAMES[*]}). Continuing, but proceed with caution."
    else
        log_info "Detected supported Ubuntu release: ${PRETTY_NAME:-unknown}."
    fi
}

require_network_connectivity() {
    if ! curl --silent --fail --max-time 5 https://download.docker.com > /dev/null; then
        fail_with_message "No outbound network connectivity to download.docker.com. Check firewall/proxy settings and retry."
    fi
    log_info "Outbound network connectivity confirmed."
}

require_compose_file_present() {
    if [[ ! -f "${GENBI_COMPOSE_FILE}" ]]; then
        fail_with_message "Could not find docker-compose.yml at ${GENBI_COMPOSE_FILE}. " \
                           "Run this script from the root of a cloned copy of the repository."
    fi
}

# ---------------------------------------------------------------------------
# Step 1: System update (best-effort, never fatal on its own)
# ---------------------------------------------------------------------------
update_system_packages() {
    log_info "Updating system package index..."
    export DEBIAN_FRONTEND=noninteractive

    if ! apt-get update -y >> "${GENBI_LOG_FILE}" 2>&1; then
        fail_with_message "apt-get update failed. Check network/mirror configuration and retry."
    fi

    if ! apt-get install -y --no-install-recommends \
        ca-certificates curl gnupg lsb-release openssl >> "${GENBI_LOG_FILE}" 2>&1; then
        fail_with_message "Failed to install baseline prerequisite packages (curl, gnupg, openssl)."
    fi

    log_info "Baseline packages installed successfully."
}

# ---------------------------------------------------------------------------
# Step 2: Install Docker Engine + Compose plugin (idempotent)
# ---------------------------------------------------------------------------
install_docker() {
    if command -v docker &> /dev/null && docker compose version &> /dev/null; then
        log_info "Docker and Docker Compose plugin already installed; skipping installation."
        return 0
    fi

    log_info "Installing Docker Engine via the official Docker apt repository..."

    install -m 0755 -d /etc/apt/keyrings

    if [[ ! -f /etc/apt/keyrings/docker.gpg ]]; then
        curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
            | gpg --dearmor -o /etc/apt/keyrings/docker.gpg \
            || fail_with_message "Failed to download or import Docker's GPG signing key."
        chmod a+r /etc/apt/keyrings/docker.gpg
    fi

    local ubuntu_codename
    ubuntu_codename="$(. /etc/os-release && echo "${VERSION_CODENAME}")"
    local arch
    arch="$(dpkg --print-architecture)"

    echo \
        "deb [arch=${arch} signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu ${ubuntu_codename} stable" \
        > /etc/apt/sources.list.d/docker.list

    if ! apt-get update -y >> "${GENBI_LOG_FILE}" 2>&1; then
        fail_with_message "apt-get update failed after adding the Docker repository."
    fi

    if ! apt-get install -y --no-install-recommends \
        docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin \
        >> "${GENBI_LOG_FILE}" 2>&1; then
        fail_with_message "Failed to install Docker packages. Check ${GENBI_LOG_FILE} for apt output."
    fi

    systemctl enable --now docker >> "${GENBI_LOG_FILE}" 2>&1 \
        || fail_with_message "Failed to enable/start the docker.service systemd unit."

    if ! docker compose version &> /dev/null; then
        fail_with_message "Docker Compose plugin installation appears to have succeeded but 'docker compose version' still fails."
    fi

    log_info "Docker Engine and Compose plugin installed and running."
}

grant_docker_group_to_invoking_user() {
    local invoking_user="${SUDO_USER:-}"
    if [[ -z "${invoking_user}" ]]; then
        log_warn "Could not determine the non-root invoking user; skipping docker group assignment."
        return 0
    fi

    if ! getent group docker &> /dev/null; then
        groupadd docker
    fi

    if ! id -nG "${invoking_user}" | grep -qw docker; then
        usermod -aG docker "${invoking_user}"
        log_info "Added user '${invoking_user}' to the 'docker' group. They must log out and back in for this to take effect."
    fi
}

# ---------------------------------------------------------------------------
# Step 3: Generate secrets (idempotent -- never overwrites an existing .env)
# ---------------------------------------------------------------------------
generate_secret_urlsafe() {
    openssl rand -base64 48 | tr -d '\n' | tr '+/' '-_'
}

generate_fernet_compatible_key() {
    # Fernet keys must be exactly 32 url-safe base64-encoded bytes.
    openssl rand -base64 32 | tr '+/' '-_' | head -c 44
    echo
}

generate_environment_file() {
    if [[ -f "${GENBI_ENV_FILE}" ]]; then
        log_info "Existing .env file found at ${GENBI_ENV_FILE}; leaving secrets untouched. " \
                  "Delete this file manually only if you intend to fully reset credentials."
        return 0
    fi

    log_info "Generating fresh secrets for a new installation..."

    local admin_db_password
    admin_db_password="$(generate_secret_urlsafe)"
    local jwt_signing_key
    jwt_signing_key="$(generate_secret_urlsafe)"
    local master_encryption_key
    master_encryption_key="$(generate_fernet_compatible_key)"

    umask 077
    cat > "${GENBI_ENV_FILE}" <<EOF
# Auto-generated by install.sh on $(date --iso-8601=seconds)
# DO NOT commit this file to Git (it is already listed in .gitignore).
# Back it up securely and separately from the Admin Database backups --
# this file is the only copy of the encryption key that protects stored
# target-database credentials.

GENBI_IMAGE_TAG=latest

ADMIN_DB_NAME=genbi_admin
ADMIN_DB_USER=genbi_admin_user
ADMIN_DB_PASSWORD=${admin_db_password}

GENBI_JWT_SIGNING_KEY=${jwt_signing_key}
GENBI_MASTER_ENCRYPTION_KEY=${master_encryption_key}

# Update these to your real domains once DNS is pointed at this server.
# Left as .localhost defaults so a fresh install works immediately for
# local testing without any DNS configuration.
GENBI_OPENUI_DOMAIN=app.localhost
GENBI_ADMIN_DOMAIN=admin.localhost
GENBI_CORS_ALLOWED_ORIGINS=http://app.localhost,http://admin.localhost
EOF

    chmod 600 "${GENBI_ENV_FILE}"
    log_info "Secrets generated and written to ${GENBI_ENV_FILE} (permissions 600)."
}

# ---------------------------------------------------------------------------
# Step 4: Build and deploy the compose stack
# ---------------------------------------------------------------------------
deploy_compose_stack() {
    log_info "Building container images from source (this may take several minutes on first install)..."
    if ! docker compose --env-file "${GENBI_ENV_FILE}" -f "${GENBI_COMPOSE_FILE}" build >> "${GENBI_LOG_FILE}" 2>&1; then
        fail_with_message "Failed to build one or more container images. Check ${GENBI_LOG_FILE} for details."
    fi

    log_info "Pulling third-party images (Postgres, Redis, Traefik)..."
    if ! docker compose --env-file "${GENBI_ENV_FILE}" -f "${GENBI_COMPOSE_FILE}" pull \
        admin_database redis_cache reverse_proxy >> "${GENBI_LOG_FILE}" 2>&1; then
        fail_with_message "Failed to pull third-party images. Check ${GENBI_LOG_FILE} for details."
    fi

    log_info "Starting the GenBI platform stack..."
    if ! docker compose --env-file "${GENBI_ENV_FILE}" -f "${GENBI_COMPOSE_FILE}" up -d >> "${GENBI_LOG_FILE}" 2>&1; then
        fail_with_message "Failed to start the compose stack. Check ${GENBI_LOG_FILE} for details, " \
                           "and inspect with: docker compose -f ${GENBI_COMPOSE_FILE} logs"
    fi

    log_info "Stack started. Verifying container health..."
    sleep 5
    docker compose --env-file "${GENBI_ENV_FILE}" -f "${GENBI_COMPOSE_FILE}" ps | tee -a "${GENBI_LOG_FILE}"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    touch "${GENBI_LOG_FILE}"
    log_info "=== GenBI Platform installation started ==="

    require_root
    require_supported_ubuntu
    require_network_connectivity
    require_compose_file_present
    update_system_packages
    install_docker
    grant_docker_group_to_invoking_user
    generate_environment_file
    deploy_compose_stack

    log_info "=== Installation complete ==="
    log_info "By default the OpenUI is served at http://app.localhost and the Admin Panel at http://admin.localhost."
    log_info "Add matching entries to your DNS or /etc/hosts, or edit GENBI_OPENUI_DOMAIN / GENBI_ADMIN_DOMAIN in ${GENBI_ENV_FILE}."
    log_info "Secrets are stored at ${GENBI_ENV_FILE} -- back this file up securely and separately from database backups."
    log_info "To update the platform later: git pull, then re-run this script, or run:"
    log_info "    docker compose --env-file ${GENBI_ENV_FILE} -f ${GENBI_COMPOSE_FILE} build && docker compose --env-file ${GENBI_ENV_FILE} -f ${GENBI_COMPOSE_FILE} up -d"
}

main "$@"
