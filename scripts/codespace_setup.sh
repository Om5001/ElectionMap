#!/usr/bin/env bash
# One-shot setup for a GitHub Codespace (or any Linux/macOS shell).
#   bash scripts/codespace_setup.sh                 install deps, run tests, commit + push
#   bash scripts/codespace_setup.sh --install-only  just install deps (used by the dev container)
set -u
cd "$(dirname "$0")/.."

echo "==> Installing Python packages"
python3 -m pip install --quiet -r requirements.txt pytest 2>/dev/null \
  || python3 -m pip install --quiet --user -r requirements.txt pytest \
  || python3 -m pip install --quiet --break-system-packages -r requirements.txt pytest
[ "${1:-}" = "--install-only" ] && exit 0

echo "==> Checking the folder layout"
missing=0
for p in forecast pm site data scripts tests README.md requirements.txt .github/workflows/forecast.yml; do
  [ -e "$p" ] || { echo "   MISSING: $p"; missing=1; }
done
[ $missing = 1 ] && { echo "Files are not in the right place - see README 'Open in a Codespace'."; exit 1; }
echo "   ok"

if [ -n "${GITHUB_REPOSITORY:-}" ]; then
  sed -i "s#OWNER/REPO#${GITHUB_REPOSITORY}#" README.md
fi

echo "==> Running tests"
python3 -m pytest -q || { echo "Tests failed - send the output above to Claude."; exit 1; }

echo "==> Committing and pushing"
git add -A
git -c user.name="${GIT_AUTHOR_NAME:-$(git config user.name || echo codespace)}" \
    -c user.email="${GIT_AUTHOR_EMAIL:-$(git config user.email || echo codespace@users.noreply.github.com)}" \
    commit -qm "Election Forest" || echo "   (nothing new to commit)"
git push -q && echo "   pushed" || echo "   push failed - run: git push"

echo
echo "Done. Start the site with:  python3 scripts/live_server.py --every 90"
