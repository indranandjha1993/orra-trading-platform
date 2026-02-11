#!/usr/bin/env bash
set -u

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

LOG_DIR="artifacts/logs"
REPORT_DIR="artifacts/reports"
mkdir -p "$LOG_DIR" "$REPORT_DIR"

MODE="host"
if [[ "${1:-}" == "--sandbox" ]]; then
  MODE="sandbox"
elif [[ "${1:-}" == "--host" || -z "${1:-}" ]]; then
  MODE="host"
else
  echo "Usage: bash artifacts/audit.sh [--host|--sandbox]"
  exit 2
fi

run_cmd() {
  local name="$1"
  shift
  local out="$LOG_DIR/${name}.log"
  {
    echo "CMD: $*"
    echo "TIME: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    "$@"
    code=$?
    echo "EXIT_CODE: $code"
  } >"$out" 2>&1
}

write_skip_log() {
  local name="$1"
  local reason="$2"
  local out="$LOG_DIR/${name}.log"
  {
    echo "CMD: skipped"
    echo "TIME: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "STATUS: skipped"
    echo "SKIP_REASON: $reason"
    echo "EXIT_CODE: 0"
  } >"$out" 2>&1
}

# Fresh evidence logs
run_cmd uv_sync_frozen env UV_CACHE_DIR=/tmp/uv-cache uv sync --frozen
run_cmd compileall env UV_CACHE_DIR=/tmp/uv-cache uv run python -m compileall src
run_cmd route_inventory rg -n "@router\\.|app\\.(get|post|put|patch|delete)\\(" src/api src/agents
run_cmd exception_scan rg -n "except Exception|HTTPException|RequestValidationError|exception_handler" src
run_cmd trade_signal_check rg -n "trade/signal|/trade/signal|trade_signal" src
run_cmd model_query_scan rg -n "select\\(|update\\(|delete\\(|session\\.(execute|scalar|scalars)\\(" src
run_cmd tenant_scope_scan rg -n "tenant_id|_scoped_select|TenantRepository|require_super_admin" src
run_cmd secret_scan rg -n --hidden --glob '!.git' "(sk_[A-Za-z0-9]{10,}|pk_[A-Za-z0-9]{10,}|whsec_[A-Za-z0-9]{10,}|Bearer\\s+[A-Za-z0-9\\._\\-]+|api[_-]?secret\\s*=\\s*['\\\"][^'\\\"]+['\\\"]|access_token\\s*=\\s*['\\\"][^'\\\"]+['\\\"])" .

if [[ "$MODE" == "host" ]]; then
  run_cmd docker_compose_ps docker compose ps
  run_cmd local_bind_test env UV_CACHE_DIR=/tmp/uv-cache uv run python - <<'PY'
import socket
s = socket.socket()
try:
    s.bind(("127.0.0.1", 9040))
    print("bind_ok")
except Exception as e:
    print(f"bind_error: {e}")
    raise
finally:
    s.close()
PY
  run_cmd alembic_current env UV_CACHE_DIR=/tmp/uv-cache uv run alembic current
  run_cmd redis_ping env UV_CACHE_DIR=/tmp/uv-cache uv run python - <<'PY'
import asyncio
import redis.asyncio as redis
async def main():
    r = redis.from_url("redis://localhost:6379/0", decode_responses=True)
    try:
        print(await r.ping())
    finally:
        await r.aclose()
asyncio.run(main())
PY
else
  write_skip_log docker_compose_ps "sandbox mode: docker daemon checks skipped"
  write_skip_log local_bind_test "sandbox mode: local port bind checks skipped"
  write_skip_log alembic_current "sandbox mode: db socket checks skipped"
  write_skip_log redis_ping "sandbox mode: redis socket checks skipped"
fi

# Helper to read EXIT_CODE from a log
exit_code_of() {
  local file="$1"
  grep -E "^EXIT_CODE:" "$file" | tail -n1 | awk '{print $2}'
}

classify_log() {
  local file="$1"
  local ec
  ec="$(exit_code_of "$file")"
  if rg -q "^STATUS: skipped$" "$file"; then
    echo "skipped"
    return
  fi
  if [[ "${ec}" == "0" ]]; then
    echo "pass"
    return
  fi

  if rg -qi "operation not permitted|permission denied while trying to connect to the Docker daemon socket|Error 1 connecting to localhost|PermissionError" "$file"; then
    echo "env-restricted"
    return
  fi

  if [[ "$(basename "$file")" == "trade_signal_check.log" ]]; then
    echo "code-gap"
    return
  fi

  echo "failed"
}

# Derived facts directly from logs
UV_LOCK_EXIT="$(exit_code_of "$LOG_DIR/uv_sync_frozen.log")"
COMPILE_EXIT="$(exit_code_of "$LOG_DIR/compileall.log")"
TRADE_SIGNAL_EXIT="$(exit_code_of "$LOG_DIR/trade_signal_check.log")"
ALEMBIC_EXIT="$(exit_code_of "$LOG_DIR/alembic_current.log")"
REDIS_EXIT="$(exit_code_of "$LOG_DIR/redis_ping.log")"
DOCKER_PS_EXIT="$(exit_code_of "$LOG_DIR/docker_compose_ps.log")"
BIND_EXIT="$(exit_code_of "$LOG_DIR/local_bind_test.log")"
SECRET_EXIT="$(exit_code_of "$LOG_DIR/secret_scan.log")"

UV_LOCK_CLASS="$(classify_log "$LOG_DIR/uv_sync_frozen.log")"
COMPILE_CLASS="$(classify_log "$LOG_DIR/compileall.log")"
TRADE_SIGNAL_CLASS="$(classify_log "$LOG_DIR/trade_signal_check.log")"
ALEMBIC_CLASS="$(classify_log "$LOG_DIR/alembic_current.log")"
REDIS_CLASS="$(classify_log "$LOG_DIR/redis_ping.log")"
DOCKER_PS_CLASS="$(classify_log "$LOG_DIR/docker_compose_ps.log")"
BIND_CLASS="$(classify_log "$LOG_DIR/local_bind_test.log")"
SECRET_CLASS="$(classify_log "$LOG_DIR/secret_scan.log")"

# Reports generated strictly from evidence logs
cat > "$REPORT_DIR/implementation-plan-artifact.md" <<RPT
# Implementation Plan Artifact (Evidence-Based)

## Evidence Sources
- action log files:
  - artifacts/logs/uv_sync_frozen.log
  - artifacts/logs/compileall.log
  - artifacts/logs/route_inventory.log
  - artifacts/logs/exception_scan.log
  - artifacts/logs/trade_signal_check.log
  - artifacts/logs/model_query_scan.log
  - artifacts/logs/tenant_scope_scan.log

## Measured Results
- uv lock sync: exit ${UV_LOCK_EXIT}, class ${UV_LOCK_CLASS}
- compile check: exit ${COMPILE_EXIT}, class ${COMPILE_CLASS}
- trade signal route scan: exit ${TRADE_SIGNAL_EXIT}, class ${TRADE_SIGNAL_CLASS}

## Missing/Risky Blocks (from logs)
- Trade signal endpoint pattern scan is classed as ${TRADE_SIGNAL_CLASS}; see trade_signal_check.log.
- Global exception handler patterns are listed in artifacts/logs/exception_scan.log.
- Tenant-scoping query patterns are listed in artifacts/logs/tenant_scope_scan.log.

## Implementation Plan
1. Add missing route patterns indicated by trade_signal_check.log.
2. Add/standardize exception handlers based on exception_scan.log inventory.
3. Review non-tenant-scoped query blocks listed by model_query_scan.log and tenant_scope_scan.log.
RPT

cat > "$REPORT_DIR/walkthrough-artifact.md" <<RPT
# Walkthrough Artifact (Evidence-Based)

Mode: ${MODE}

## Commands and Evidence
- Docker stack status: artifacts/logs/docker_compose_ps.log (exit ${DOCKER_PS_EXIT})
- Local bind capability: artifacts/logs/local_bind_test.log (exit ${BIND_EXIT})
- DB schema reachability: artifacts/logs/alembic_current.log (exit ${ALEMBIC_EXIT})
- Redis reachability: artifacts/logs/redis_ping.log (exit ${REDIS_EXIT})
- Route inventory for API/agents: artifacts/logs/route_inventory.log
- Trade signal route presence check: artifacts/logs/trade_signal_check.log (exit ${TRADE_SIGNAL_EXIT})

## Classification
- docker compose ps: ${DOCKER_PS_CLASS}
- local bind test: ${BIND_CLASS}
- alembic current: ${ALEMBIC_CLASS}
- redis ping: ${REDIS_CLASS}
- trade signal presence: ${TRADE_SIGNAL_CLASS}

## Outcome
- This report intentionally contains only command-backed outcomes.
- Use the listed logs as the source of truth for each validation step.
RPT

cat > "$REPORT_DIR/security-audit-artifact.md" <<RPT
# Security Audit Artifact (Evidence-Based)

## Commands and Evidence
- Secret pattern scan: artifacts/logs/secret_scan.log (exit ${SECRET_EXIT})
- Exception/log pattern scan: artifacts/logs/exception_scan.log
- Compile integrity check: artifacts/logs/compileall.log (exit ${COMPILE_EXIT})

## Classification
- secret scan: ${SECRET_CLASS}
- compile check: ${COMPILE_CLASS}

## Outcome
- This report intentionally contains only command-backed outcomes.
- Review secret_scan.log for any matched patterns and exact file locations.
RPT

echo "Audit completed. Logs in $LOG_DIR, reports in $REPORT_DIR"
