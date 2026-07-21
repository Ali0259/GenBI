#!/usr/bin/env bash
#
# uninstall.sh - GenBI Platform uninstaller
#
# Two modes, chosen interactively (or via --partial / --complete flags):
#
#   PARTIAL removal: stops and removes containers and the images built from
#   this repo's Dockerfiles, but keeps your data -- the admin database
#   volume, backup snapshots, .env secrets, and the secrets/ credentials
#   file. Use this when you want to tear the stack down and reinstall or
#   rebuild from scratch without losing tenants, connections, LLM configs,
#   or your admin login.
#
#   COMPLETE removal: everything PARTIAL does, PLUS deletes every named
#   Docker volume (admin database, Redis data, backups, Traefik certs,
#   Ollama models), the secrets/ directory, and .env. This is irreversible
#   -- there is no undo once volumes are removed. Use this when you're
#   decommissioning the VM or want a genuinely clean slate.
#
# Usage:
#   sudo ./uninstall.sh              # interactive menu
#   sudo ./uninstall.sh --partial    # non-interactive partial removal
#   sudo ./uninstall.sh --complete   # non-interactive complete removal (still asks to type DELETE)

set -Eeuo pipefail
IFS=$'\n\t'

readonly REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly GENBI_ENV_FILE="${REPO_ROOT}/.env"
readonly GENBI_COMPOSE_FILE="${REPO_ROOT}/docker-compose.yml"
readonly GENBI_SECRETS_DIR="${REPO_ROOT}/secrets"

log_info()  { echo -e "[INFO]  $*"; }
log_warn()  { echo -e "[WARN]  $*" >&2; }
log_error() { echo -e "[ERROR] $*" >&2; }

fail_with_message() {
    log_error "$1"
    exit 1
}

require_root() {
    if [[ "${EUID}" -ne 0 ]]; then
        fail_with_message "This script must be run as root (try: sudo ./uninstall.sh)."
    fi
}

require_compose_file_present() {
    if [[ ! -f "${GENBI_COMPOSE_FILE}" ]]; then
        fail_with_message "Could not find docker-compose.yml at ${GENBI_COMPOSE_FILE}. " \
                           "Run this script from the root of your GenBI installation."
    fi
}

compose_cmd() {
    if [[ -f "${GENBI_ENV_FILE}" ]]; then
        docker compose --env-file "${GENBI_ENV_FILE}" -f "${GENBI_COMPOSE_FILE}" "$@"
    else
        docker compose -f "${GENBI_COMPOSE_FILE}" "$@"
    fi
}

# ---------------------------------------------------------------------------
# Partial removal: containers + locally built images, data preserved
# ---------------------------------------------------------------------------
run_partial_removal() {
    log_info "Stopping and removing containers (data volumes will be kept)..."
    compose_cmd down --rmi local

    log_info "Partial removal complete."
    log_info "Preserved: admin database volume, backups, Traefik certs, Ollama models, "
    log_info "           .env (${GENBI_ENV_FILE}), and secrets/ (${GENBI_SECRETS_DIR})."
    log_info "To reinstall from scratch with this same data: docker compose build && docker compose up -d"
    log_info "(or just re-run ./install.sh -- it will detect the existing .env and leave it untouched)."
}

# ---------------------------------------------------------------------------
# Complete removal: containers, images, ALL volumes, secrets, .env
# ---------------------------------------------------------------------------
confirm_complete_removal() {
    echo ""
    log_warn "COMPLETE REMOVAL will permanently delete:"
    log_warn "  - The admin database (all tenants, connections, LLM configs, admin accounts)"
    log_warn "  - All pre-migration backup snapshots"
    log_warn "  - Traefik TLS certificate data"
    log_warn "  - Any downloaded Ollama models"
    log_warn "  - ${GENBI_ENV_FILE} (all generated secrets)"
    log_warn "  - ${GENBI_SECRETS_DIR} (default admin credentials file)"
    echo ""
    log_warn "This CANNOT be undone. There is no backup restore path after this runs."
    echo ""
    read -r -p "Type DELETE (in capitals) to confirm complete removal, anything else to abort: " confirmation

    if [[ "${confirmation}" != "DELETE" ]]; then
        log_info "Confirmation not received. Aborting -- nothing was removed."
        exit 0
    fi
}

run_complete_removal() {
    confirm_complete_removal

    log_info "Stopping and removing containers, images, and ALL volumes..."
    compose_cmd down --rmi local --volumes --remove-orphans

    if [[ -f "${GENBI_ENV_FILE}" ]]; then
        rm -f "${GENBI_ENV_FILE}"
        log_info "Removed ${GENBI_ENV_FILE}."
    fi

    if [[ -d "${GENBI_SECRETS_DIR}" ]]; then
        rm -rf "${GENBI_SECRETS_DIR}"
        log_info "Removed ${GENBI_SECRETS_DIR}."
    fi

    echo ""
    log_info "Complete removal finished. The repository's code files (this script, docker-compose.yml, "
    log_info "backend/, frontend-*/, etc.) were left in place -- delete the whole directory yourself "
    log_info "if you want the checkout gone too: rm -rf ${REPO_ROOT}"
    echo ""
    log_info "Note: the generic third-party images (postgres:16.4-alpine, redis:7.4-alpine, "
    log_info "traefik:v3.7.8, ollama/ollama:latest) were intentionally left on this machine in case "
    log_info "other projects use them. To remove those too:"
    log_info "    docker rmi postgres:16.4-alpine redis:7.4-alpine traefik:v3.7.8 ollama/ollama:latest"
    log_info "Docker Engine itself was also left installed. To remove Docker entirely from this VM:"
    log_info "    apt-get purge -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin"
    log_info "    rm -rf /var/lib/docker /etc/docker"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    require_root
    require_compose_file_present

    local mode="${1:-}"

    if [[ "${mode}" == "--partial" ]]; then
        run_partial_removal
        return 0
    fi

    if [[ "${mode}" == "--complete" ]]; then
        run_complete_removal
        return 0
    fi

    if [[ -n "${mode}" ]]; then
        fail_with_message "Unknown option '${mode}'. Use --partial, --complete, or no argument for the interactive menu."
    fi

    echo ""
    echo "GenBI Platform Uninstaller"
    echo "=========================="
    echo "1) Partial removal  -- stop containers, remove built images, KEEP all data (recommended for reinstalling/upgrading)"
    echo "2) Complete removal -- delete EVERYTHING including the admin database, secrets, and .env (irreversible)"
    echo "3) Cancel"
    echo ""
    read -r -p "Choose an option [1-3]: " choice

    case "${choice}" in
        1) run_partial_removal ;;
        2) run_complete_removal ;;
        3) log_info "Cancelled. Nothing was removed." ;;
        *) fail_with_message "Invalid choice '${choice}'." ;;
    esac
}

main "$@"
