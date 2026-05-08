#!/usr/bin/env bash
# Install every workspace package and service in editable / development mode.
#
# Two modes — uv (preferred) and pip fallback. The uv path is what CI and the
# Dockerfile.dev use; the pip fallback exists for engineers who already have a
# venv they want to reuse.
#
# Usage:
#   scripts/install_packages.sh             # uv sync (or pip fallback)
#   scripts/install_packages.sh --pip       # force pip
#   scripts/install_packages.sh --check     # verify imports without installing

set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$PWD"

MODE=auto
if [[ "${1:-}" == "--pip" ]]; then
  MODE=pip
elif [[ "${1:-}" == "--check" ]]; then
  MODE=check
fi

PACKAGES=(
  packages/schemas
  packages/obs
  packages/kafka-client
  packages/auth-lib
  packages/audit-lib
  packages/feature-client
  packages/graph-client
  packages/testing
)

SERVICES=(
  services/ingest-momo
  services/ingest-voice
  services/ingest-sms
  services/stream-features
  services/stream-graph
  services/brain-behavioural
  services/brain-content
  services/decisions
  services/action-tier1
  services/action-tier2
  services/api-noc
  services/api-customer
  services/compliance
)

# ---- check mode ------------------------------------------------------------
if [[ "$MODE" == "check" ]]; then
  echo "==> Verifying every workspace package has a valid pyproject.toml"
  fail=0
  for pkg in "${PACKAGES[@]}" "${SERVICES[@]}"; do
    if [[ ! -f "$ROOT/$pkg/pyproject.toml" ]]; then
      echo "  ✗ $pkg/pyproject.toml MISSING"
      fail=1
    else
      name=$(awk -F'"' '/^name = /{print $2; exit}' "$ROOT/$pkg/pyproject.toml")
      echo "  ✓ $pkg ($name)"
    fi
  done

  if [[ $fail -ne 0 ]]; then
    echo "FAIL: missing pyproject.toml files"
    exit 1
  fi

  echo
  echo "==> Verifying declared workspace deps resolve to real packages"
  declared=()
  for pkg in "${PACKAGES[@]}"; do
    name=$(awk -F'"' '/^name = /{print $2; exit}' "$ROOT/$pkg/pyproject.toml")
    declared+=("$name")
  done

  fail=0
  for svc in "${SERVICES[@]}"; do
    awk '/dependencies = \[/,/^]/' "$ROOT/$svc/pyproject.toml" \
      | grep -oE '"fraudnet-[a-z]+"' \
      | tr -d '"' \
      | while read -r dep; do
          ok=0
          for d in "${declared[@]}"; do
            [[ "$d" == "$dep" ]] && ok=1 && break
          done
          if [[ $ok -eq 0 ]]; then
            echo "  ✗ $svc → $dep (not in workspace)"
            exit 1
          fi
        done
    echo "  ✓ $svc deps resolve"
  done
  echo
  echo "==> Verifying every 'from fraudnet.X' import has a declared workspace dep"
  # Map fraudnet.<subpkg> → required workspace package name. Case form keeps
  # this script bash-3 compatible (macOS default).
  subpkg_to_pkg() {
    case "$1" in
      schemas)  echo fraudnet-schemas ;;
      obs)      echo fraudnet-obs ;;
      kafka)    echo fraudnet-kafka ;;
      audit)    echo fraudnet-audit ;;
      auth)     echo fraudnet-auth ;;
      features) echo fraudnet-features ;;
      graph)    echo fraudnet-graph ;;
      testing)  echo fraudnet-testing ;;
      *)        echo "" ;;
    esac
  }

  fail=0
  for svc in "${SERVICES[@]}"; do
    name=$(basename "$svc")
    if [[ ! -d "$ROOT/$svc/src" ]]; then continue; fi

    declared=$(grep -E '"fraudnet-' "$ROOT/$svc/pyproject.toml" | grep -oE 'fraudnet-[a-z]+' | sort -u)
    imports=$(grep -rh "^from fraudnet\." "$ROOT/$svc/src" 2>/dev/null \
              | awk '{print $2}' | awk -F. '{print $2}' | sort -u)

    missing=""
    for sub in $imports; do
      need=$(subpkg_to_pkg "$sub")
      if [[ -z "$need" ]]; then continue; fi
      if ! echo "$declared" | grep -q "^$need$"; then
        missing="$missing $need"
      fi
    done
    if [[ -n "$missing" ]]; then
      echo "  ✗ $name imports from{$missing } but does not declare them"
      fail=1
    else
      echo "  ✓ $name imports match declared deps"
    fi
  done

  if [[ $fail -ne 0 ]]; then
    echo "FAIL: undeclared cross-package imports"
    exit 1
  fi
  echo
  echo "OK: workspace package graph is consistent"
  exit 0
fi

# ---- install mode ----------------------------------------------------------
if command -v uv >/dev/null 2>&1 && [[ "$MODE" != "pip" ]]; then
  echo "==> Using uv to sync the workspace (--all-packages)"
  uv sync --all-packages --all-extras 2>/dev/null || uv sync --all-packages
  echo "OK: uv sync complete"
  exit 0
fi

echo "==> uv not available (or --pip forced); using pip editable install"

if [[ -z "${VIRTUAL_ENV:-}" ]]; then
  echo "  ! No active venv detected. Activate one before running this script." >&2
  echo "    e.g.: python3.12 -m venv .venv && source .venv/bin/activate" >&2
  exit 1
fi

python -m pip install --upgrade pip wheel hatchling

# Topological order: schemas + obs first (no internal deps), then leaves.
ORDERED=(
  packages/schemas
  packages/obs
  packages/kafka-client
  packages/auth-lib
  packages/audit-lib
  packages/feature-client
  packages/graph-client
  packages/testing
  "${SERVICES[@]}"
)

for pkg in "${ORDERED[@]}"; do
  echo "==> pip install -e $pkg"
  python -m pip install -e "$ROOT/$pkg"
done

echo
echo "OK: workspace installed. 'python -c \"import fraudnet.schemas\"' should work."
