#!/usr/bin/env bash
# Runs the payment-layer tests. Pass a python interpreter as $1, or set PYTHON,
# otherwise the backend venv is used when present and python3 as a last resort.
set -u
cd "$(dirname "$0")/.."

PY="${1:-${PYTHON:-}}"
if [ -z "$PY" ]; then
    if [ -x "venv/bin/python" ]; then PY="venv/bin/python"; else PY="python3"; fi
fi

echo "interpreter: $PY"
failed=0
for t in tests/test_providers.py tests/test_webhook_e2e.py tests/test_default_regression.py; do
    printf '%-36s ' "$(basename "$t")"
    if out="$("$PY" "$t" 2>&1)"; then
        echo "PASS"
    else
        echo "FAIL"
        printf '%s\n' "$out" | tail -20 | sed 's/^/    /'
        failed=1
    fi
done
exit "$failed"
