#!/bin/sh
# Install ratchet for every harness on this machine:
#   - links `ratchet` into ~/.local/bin (Codex, Cursor, OpenCode, Pi, T3 terminals and scripts)
#   - installs the Claude Code plugin (skills + Stop gate) when `claude` is available
set -eu
here=$(cd "$(dirname "$0")/.." && pwd)
mkdir -p "$HOME/.local/bin"
ln -sf "$here/plugin/bin/ratchet" "$HOME/.local/bin/ratchet"
echo "linked $HOME/.local/bin/ratchet -> $here/plugin/bin/ratchet"
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) echo "note: add ~/.local/bin to PATH" ;; esac
if command -v claude >/dev/null 2>&1; then
  claude plugin marketplace add "$here" >/dev/null 2>&1 || true
  claude plugin install ratchet@cosmyk && echo "Claude Code plugin installed (ratchet@cosmyk)"
fi
