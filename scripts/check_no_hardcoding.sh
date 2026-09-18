#!/usr/bin/env bash
# The public sample cases are validation examples only. They must never leak into the service.
set -euo pipefail

status=0

if grep -rn --include='*.py' -E 'SAMPLE-[0-9]' app/; then
    echo "FAIL: app/ references a public sample case id"
    status=1
fi

if grep -rn --include='*.py' -iE 'public_cases|expected_output|expected_reference' app/; then
    echo "FAIL: app/ references the public sample case pack"
    status=1
fi

for phrase in "rooftop solar panels" "sports office" "cafeteria menu" "library is extending"; do
    if grep -rni --include='*.py' "$phrase" app/; then
        echo "FAIL: app/ contains public sample note wording: $phrase"
        status=1
    fi
done

if [ "$status" -eq 0 ]; then
    echo "OK: app/ contains no public sample case data"
fi
exit "$status"
