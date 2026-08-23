#!/usr/bin/env bash
# Fail on bare parameters compared against NULL in SQL.
#
# Postgres cannot infer a parameter's type from `$n IS NULL` alone and raises
# AmbiguousParameter at runtime — which means it only shows up when that exact
# filter is exercised, i.e. usually in front of a user. This bug shipped three
# times in this repo before the pattern got checked mechanically.
#
# Correct form:  %(name)s::numeric IS NULL
#
# Run manually:      ./scripts/check-sql-params.sh
# Install as a hook: it is called by scripts/check-no-leaks.sh (pre-commit)
set -uo pipefail
cd "$(git rev-parse --show-toplevel)" || exit 1

# A parameter placeholder NOT followed by :: before IS [NOT] NULL.
hits=$(grep -rnE '%\(\w+\)s[[:space:]]+IS[[:space:]]+(NOT[[:space:]]+)?NULL' \
         --include='*.py' api/ crawler/ 2>/dev/null)

if [ -n "$hits" ]; then
  echo "UNCAST SQL PARAMETER compared to NULL (will raise AmbiguousParameter):"
  echo "$hits" | sed 's/^/    /'
  echo
  echo "Add an explicit cast, e.g. %(name)s::bigint IS NOT NULL"
  exit 1
fi

echo "sql-param check: clean"
