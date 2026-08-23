#!/usr/bin/env bash
# Fail if anything git would track names a real target site or a secret.
#
# The whole privacy design rests on target details living only in gitignored
# files. Prose drifts, though — a site name mentioned in a README months later
# leaks just as effectively as one in code. This makes the check mechanical.
#
# Run manually:      ./scripts/check-no-leaks.sh
# Install as a hook: ln -sf ../../scripts/check-no-leaks.sh .git/hooks/pre-commit
set -uo pipefail
cd "$(git rev-parse --show-toplevel)" || exit 1

# Domains and identifiers that must never appear in tracked files.
# Derived automatically from your local source configs, plus explicit extras.
PATTERNS_FILE=$(mktemp)
trap 'rm -f "$PATTERNS_FILE"' EXIT

# Every host mentioned in a (gitignored) source config becomes a forbidden term.
if compgen -G "config/sources/*.y*ml" > /dev/null; then
  grep -rhoE 'https?://[A-Za-z0-9.-]+' config/sources/*.y*ml 2>/dev/null \
    | sed -E 's#https?://##' | sort -u >> "$PATTERNS_FILE"

  # ...and the bare site name, without www or the TLD. Blocking only the full
  # host misses a script whose default argument is the name alone, and that
  # identifies the target just as clearly.
  grep -rhoE 'https?://[A-Za-z0-9.-]+' config/sources/*.y*ml 2>/dev/null \
    | sed -E 's#https?://(www\.)?##; s#\.[A-Za-z]{2,}(\.[A-Za-z]{2,})?$##' \
    | grep -vE '^(example|localhost|e)$' | sort -u >> "$PATTERNS_FILE"

  # Source keys and search labels. These end up in --source flags, script
  # defaults and log lines, and they identify the target by name.
  #
  # Only list-item labels ("- label:") are searches. A bare "label:" nested
  # under detail: is a field caption on the page — "Cijena", "Lokacija" — which
  # is a common Croatian word, not an identifier, and blocking those would make
  # the check impossible to satisfy.
  sed -nE 's/^[[:space:]]*key:[[:space:]]*"?([A-Za-z0-9._-]+)"?.*/\1/p' \
    config/sources/*.y*ml 2>/dev/null | sort -u >> "$PATTERNS_FILE"
  sed -nE 's/^[[:space:]]*-[[:space:]]*label:[[:space:]]*"?([A-Za-z0-9._-]+)"?.*/\1/p' \
    config/sources/*.y*ml 2>/dev/null | sort -u >> "$PATTERNS_FILE"

  # Place names from the searches actually being tracked. These say where the
  # user is house-hunting, which is theirs to disclose, not ours.
  grep -rhoE '[?&][A-Za-z][A-Za-z0-9_%.-]*([Ii][Dd]s|[Ii][Dd])(%5[Dd])?=[^&"[:space:]]*' \
    config/sources/*.y*ml 2>/dev/null | sed -E 's/^[^=]*=//' \
    | tr ',' '\n' | sort -u >> "$PATTERNS_FILE"
fi

# Extra terms worth blocking regardless of what the configs happen to contain.
# Only real-looking secrets: .env.example is *supposed* to carry placeholders,
# so match the shape of an actual key rather than the variable name.
cat >> "$PATTERNS_FILE" <<'EOF'
sk-ant-[A-Za-z0-9_-]\{8,\}
EOF

# Files that legitimately contain example hostnames or the patterns themselves.
EXCLUDES=':!config/sources/example.yaml.template :!scripts/check-no-leaks.sh :!.env.example'

# Ignore the placeholder domain used throughout the docs and template, and any
# term too short or too generic to mean anything ("api", "id", a bare number).
sed -i '/^classifieds\.example\.com$/d;/^example\.com$/d;/^e\.test$/d' "$PATTERNS_FILE"
sed -i -E '/^.{0,4}$/d; /^[0-9a-f-]{8,}$/d' "$PATTERNS_FILE"

# A site whose name is an ordinary word cannot be blocked as a bare word: this
# repo has SQL indexes, an index.html and a nginx index directive. The full
# host and the hyphenated source key still are blocked, which is what actually
# identifies a target — "index" on its own does not.
cat > "$PATTERNS_FILE.stop" <<'STOP'
index
data
search
home
market
shop
news
auto
mail
oglasi
STOP
grep -vixF -f "$PATTERNS_FILE.stop" "$PATTERNS_FILE" > "$PATTERNS_FILE.keep" || true
mv "$PATTERNS_FILE.keep" "$PATTERNS_FILE"
rm -f "$PATTERNS_FILE.stop"
sort -u -o "$PATTERNS_FILE" "$PATTERNS_FILE"

if [ ! -s "$PATTERNS_FILE" ]; then
  echo "no-leak check: nothing to look for (no source configs yet) — ok"
  exit 0
fi

status=0
while IFS= read -r pattern; do
  [ -z "$pattern" ] && continue
  # Search the INDEX, not just tracked files. On a repo whose first commit has
  # not landed yet nothing is tracked, so a working-tree-only search reports
  # "clean" no matter what is about to be committed.
  if hits=$( { git grep -l -i --cached -- "$pattern" -- . $EXCLUDES;
               git grep -l -i         -- "$pattern" -- . $EXCLUDES; } 2>/dev/null \
             | sort -u) && [ -n "$hits" ]; then
    echo "LEAK: '$pattern' appears in tracked file(s):"
    echo "$hits" | sed 's/^/    /'
    status=1
  fi
done < "$PATTERNS_FILE"

# Chain the SQL parameter check into the same pre-commit run.
if [ -x ./scripts/check-sql-params.sh ]; then
  ./scripts/check-sql-params.sh || status=1
fi

if [ "$status" -eq 0 ]; then
  echo "no-leak check: clean ($(wc -l < "$PATTERNS_FILE") term(s) checked)"
else
  echo
  echo "Move the offending text into a gitignored file (config/sources/*.yaml)"
  echo "or reword it. Commit blocked."
fi
exit "$status"
