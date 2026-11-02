#!/usr/bin/env bash
# Export last week's matchup summary to summaries/ and push it to GitHub.
# Run on Monday morning, after ESPN has rolled over to the new week.
#
# Needs: a clone of this repo with a .env (ESPN_LEAGUE_ID, ESPN_S2, ESPN_SWID...),
# a virtualenv with requirements installed, and git push access from this machine.
set -euo pipefail

REPO_DIR="${REPO_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
PYTHON="${PYTHON:-$REPO_DIR/.venv/bin/python}"
BRANCH="${BRANCH:-main}"
EXPORT_ARGS="${EXPORT_ARGS:---previous}"   # e.g. EXPORT_ARGS="--week 3" to redo one week

cd "$REPO_DIR"
git pull --rebase --quiet origin "$BRANCH"

# shellcheck disable=SC2086
path="$("$PYTHON" -m fantasy_recap $EXPORT_ARGS --export summaries)"
echo "wrote $path"

git add "$path"
if git diff --cached --quiet; then
  echo "summary unchanged, nothing to push"
  exit 0
fi
git -c user.name="${GIT_AUTHOR_NAME:-fantasy-recap bot}" \
    -c user.email="${GIT_AUTHOR_EMAIL:-fantasy-recap@localhost}" \
    commit --quiet -m "Weekly summary: $(basename "$path" .json)"
git push --quiet origin "$BRANCH"
echo "pushed $path"
