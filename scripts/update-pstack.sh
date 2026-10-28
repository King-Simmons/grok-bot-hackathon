#!/usr/bin/env bash
# Refresh vendor/pstack from the harness-neutral mirror and relink project skills.
# Usage: scripts/update-pstack.sh [git-ref]
# Default ref is main. Override the clone URL with PSTACK_URL.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
URL="${PSTACK_URL:-https://github.com/backnotprop/pstack.git}"
REF="${1:-main}"
DEST="$ROOT/vendor/pstack"
workdir="$(mktemp -d)"
trap 'rm -rf "$workdir"' EXIT

git clone --depth 1 --branch "$REF" "$URL" "$workdir/pstack"
mirror_commit="$(git -C "$workdir/pstack" rev-parse HEAD)"
version="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("version",""))' "$workdir/pstack/plugin.json")"
subject="$(git -C "$workdir/pstack" log -1 --format=%s)"
upstream_commit=""
if [[ "$subject" =~ ([0-9a-f]{7,40}) ]]; then
  upstream_commit="${BASH_REMATCH[1]}"
fi

mkdir -p "$DEST"
tar -C "$workdir/pstack" \
  --exclude .git \
  --exclude automations \
  --exclude mirror-assets \
  --exclude NOTICE \
  --exclude UPSTREAM \
  -cf - . | tar -C "$DEST" -xf -

# Drop vendored paths the mirror no longer ships, but keep our NOTICE and UPSTREAM.
python3 - "$workdir/pstack" "$DEST" <<'PY'
import os, sys
src, dest = sys.argv[1], sys.argv[2]
keep = {"NOTICE", "UPSTREAM"}
def names(root):
    out = set()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        rel = os.path.relpath(dirpath, root)
        if rel == ".":
            rel = ""
        for name in dirnames + filenames:
            path = os.path.join(rel, name) if rel else name
            out.add(path)
    return out
skip_roots = {"automations", "mirror-assets", ".git"}
incoming = {p for p in names(src) if p.split(os.sep, 1)[0] not in skip_roots}
for dirpath, dirnames, filenames in os.walk(dest, topdown=False):
    rel = os.path.relpath(dirpath, dest)
    if rel == ".":
        rel = ""
    for name in filenames:
        path = os.path.join(rel, name) if rel else name
        if path in keep or path in incoming:
            continue
        os.remove(os.path.join(dest, path))
    for name in dirnames:
        path = os.path.join(rel, name) if rel else name
        full = os.path.join(dest, path)
        if path in incoming or os.listdir(full):
            continue
        os.rmdir(full)
PY

cat > "$DEST/UPSTREAM" <<EOF
mirror_repo=$URL
mirror_commit=$mirror_commit
upstream_repo=https://github.com/cursor/plugins
upstream_path=pstack
upstream_commit=$upstream_commit
version=$version
EOF

cat > "$DEST/NOTICE" <<EOF
pstack
Copyright (c) 2026 Lauren Tan

This directory is a vendored copy of pstack, Lauren Tan's (poteto) Agent
Skills stack. It is MIT-licensed. The license is in LICENSE beside this file.
Keep that license and this notice with any copy.

Source of this tree (harness-neutral mirror):
  $URL
  commit $mirror_commit

Upstream that mirror tracks:
  https://github.com/cursor/plugins/tree/main/pstack
  commit $upstream_commit
  plugin version $version

The mirror commit subject was: $subject

scripts/update-pstack.sh refreshes this directory from the mirror and rewrites
UPSTREAM. Do not edit files under vendor/pstack by hand. The Lofi News Radio
verification skill is not part of this tree. It lives at
.cursor/skills/verify-lofi-news-radio/ and is linked into the other harness
skill folders.
EOF

"$ROOT/scripts/link-pstack.sh"
echo "pstack updated to mirror $mirror_commit (upstream $upstream_commit, version $version)"
