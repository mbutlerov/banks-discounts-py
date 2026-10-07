#!/bin/sh
# Ejecutar en el servidor Linux: sh scripts/backup-production.sh [directorio]
# DEPLOY_ENV_FILE y COMPOSE_PROJECT_NAME permiten elegir el stack de producción.
set -eu
umask 077

if [ "$#" -gt 1 ]; then
    printf 'Uso: sh scripts/backup-production.sh [directorio-de-backups]\n' >&2
    exit 2
fi

SCRIPT_DIR=$(CDPATH= cd "$(dirname "$0")" && pwd -P)
PROJECT_ROOT=$(CDPATH= cd "$SCRIPT_DIR/.." && pwd -P)
cd "$PROJECT_ROOT"

ENV_FILE=${DEPLOY_ENV_FILE:-deploy.env}
PROJECT_NAME=${COMPOSE_PROJECT_NAME:-banks-prod}
BACKUP_ROOT=${1:-"$PROJECT_ROOT/.local/backups/production"}

if [ ! -f "$ENV_FILE" ]; then
    printf 'No existe el archivo de entorno %s. Configurá DEPLOY_ENV_FILE.\n' "$ENV_FILE" >&2
    exit 1
fi

for executable in docker tar sha256sum mktemp flock; do
    if ! command -v "$executable" >/dev/null 2>&1; then
        printf 'Falta el comando requerido: %s\n' "$executable" >&2
        exit 1
    fi
done

compose() {
    docker compose --env-file "$ENV_FILE" --project-name "$PROJECT_NAME" \
        -f "$PROJECT_ROOT/docker-compose.yml" \
        -f "$PROJECT_ROOT/docker-compose.prod.yml" --profile scraping "$@"
}

if [ -z "$(compose ps -q db)" ]; then
    printf 'PostgreSQL no está activo en el proyecto %s.\n' "$PROJECT_NAME" >&2
    exit 1
fi
compose exec -T db sh -c 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB" >/dev/null'

mkdir -p "$BACKUP_ROOT"
BACKUP_ROOT=$(CDPATH= cd "$BACKUP_ROOT" && pwd -P)
# Evitar que dos backups cambien simultáneamente el estado del scraper.
exec 9> "$BACKUP_ROOT/.backup.lock"
if ! flock -n 9; then
    printf 'Ya hay otro backup activo en %s.\n' "$BACKUP_ROOT" >&2
    exit 1
fi
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
STAGING_DIR=$(mktemp -d "$BACKUP_ROOT/.partial-${STAMP}.XXXXXX")
STAGING_NAME=${STAGING_DIR##*/}
FINAL_DIR="$BACKUP_ROOT/backup-${STAGING_NAME#.partial-}"
RESTART_SCRAPER=0
BACKUP_COMPLETE=0

cleanup() {
    result=$?
    trap - 0 HUP INT TERM
    if [ "$RESTART_SCRAPER" -eq 1 ]; then
        if ! compose start scraper >/dev/null; then
            printf 'El backup terminó, pero no se pudo reactivar el scraper. Ejecutá docker compose con el mismo entorno y proyecto: start scraper.\n' >&2
            result=1
        fi
    fi
    if [ "$BACKUP_COMPLETE" -eq 0 ]; then
        printf 'Backup incompleto conservado en %s; no usar para restaurar.\n' "$STAGING_DIR" >&2
    fi
    exit "$result"
}
trap cleanup 0
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

# Pausar las escrituras del worker; conservar el estado si ya estaba detenido.
if [ -n "$(compose ps -q scraper)" ]; then
    RESTART_SCRAPER=1
    compose stop --timeout 90 scraper >/dev/null
fi

# No imprimir contraseñas ni ejecutar el contenido del archivo de entorno.
compose exec -T db sh -c \
    'exec pg_dump --format=custom --no-owner --no-privileges --username="$POSTGRES_USER" --dbname="$POSTGRES_DB"' \
    > "$STAGING_DIR/postgres.dump"
compose exec -T db pg_restore --list < "$STAGING_DIR/postgres.dump" \
    > "$STAGING_DIR/postgres.contents.txt"

# /app/data reúne snapshots y revisiones OCR. El contenedor temporal no arranca API.
compose run --rm --no-deps -T --entrypoint tar api -C /app/data -czf - . \
    > "$STAGING_DIR/documents.tar.gz"
tar -tzf "$STAGING_DIR/documents.tar.gz" >/dev/null

{
    printf 'created_at_utc=%s\n' "$STAMP"
    printf 'compose_project=%s\n' "$PROJECT_NAME"
    printf 'documents_container_path=/app/data\n'
    compose exec -T db postgres --version
    printf 'architecture=%s\n' "$(uname -m)"
    if command -v git >/dev/null 2>&1; then
        printf 'git_revision=%s\n' "$(git rev-parse --verify HEAD 2>/dev/null || printf unknown)"
    fi
} > "$STAGING_DIR/manifest.txt"

(
    cd "$STAGING_DIR"
    sha256sum postgres.dump postgres.contents.txt documents.tar.gz manifest.txt > SHA256SUMS
)

if [ -e "$FINAL_DIR" ]; then
    printf 'El destino ya existe: %s\n' "$FINAL_DIR" >&2
    exit 1
fi
mv "$STAGING_DIR" "$FINAL_DIR"
BACKUP_COMPLETE=1
printf 'Backup completo: %s\n' "$FINAL_DIR"
printf 'Copiá esta carpeta fuera del servidor y conservá juntos postgres.dump, documents.tar.gz y SHA256SUMS.\n'
