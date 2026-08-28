#!/bin/bash
# Render the LaunchAgent template against wherever this checkout actually lives,
# install it, and load it. Idempotent: safe to re-run after moving the checkout
# or editing the template.
#
# The path is filled in here rather than committed because this repository is
# public and the checkout lives under a private working directory. The template
# is the source of truth; the rendered plist in ~/Library/LaunchAgents is a build
# artefact, and re-running this script is how you rebuild it.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.klay.cashcow-fallback"
DEST="$HOME/Library/LaunchAgents/$LABEL.plist"

python3 - "$REPO" "$DEST" <<'PY'
import pathlib, sys
repo, dest = sys.argv[1], sys.argv[2]
tpl = pathlib.Path(repo, "ops", "com.klay.cashcow-fallback.plist.template").read_text()
pathlib.Path(dest).write_text(tpl.replace("__CHECKOUT__", repo))
PY

plutil -lint "$DEST"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$DEST"
echo "installed and loaded: $LABEL  (checkout: $REPO)"
launchctl print "gui/$(id -u)/$LABEL" | grep -E "state =|runs =|last exit"
