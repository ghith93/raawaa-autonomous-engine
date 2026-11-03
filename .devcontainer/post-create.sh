#!/usr/bin/env bash
set -euo pipefail

echo "Node: $(node --version)"
echo "npm: $(npm --version)"
echo "Python: $(python --version)"

# Pin npm-distributed CLIs to the versions verified by the repository smoke run.
npm install --global @opencode/cli@2.2.5 @openai/codex@0.166.0

# Install Google's official CLI; the installer verifies the downloaded checksum.
curl -fsSL https://antigravity.google/cli/install.sh | bash
export PATH="$HOME/.local/bin:$PATH"

echo "OpenCode: $(opencode --version)"
echo "Codex: $(codex --version)"
echo "Antigravity: $("$HOME/.local/bin/agy" --version)"

echo "Coding-agent CLIs are installed. Authentication and model access are separate steps."
