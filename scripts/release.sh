#!/usr/bin/env bash
#
# scripts/release.sh - Cuts a new version release.
#
# Keeps every version reference in the repo in sync (root VERSION,
# backend/VERSION, both frontend package.json files), reminds you to update
# CHANGELOG.md, commits, and creates an annotated git tag. Pushing that tag
# triggers .github/workflows/release.yml, which builds and publishes
# versioned images.
#
# Usage:
#   ./scripts/release.sh 1.1.0
#
# This script does NOT push anything by itself -- review the commit and tag
# it creates locally, then `git push origin main --tags` when you're ready.

set -Eeuo pipefail

readonly REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ $# -ne 1 ]]; then
    echo "Usage: $0 <new-version>   (e.g. $0 1.1.0)" >&2
    exit 1
fi

readonly NEW_VERSION="$1"

if [[ ! "${NEW_VERSION}" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "Error: version must be in MAJOR.MINOR.PATCH form (e.g. 1.1.0). Got: ${NEW_VERSION}" >&2
    exit 1
fi

cd "${REPO_ROOT}"

if [[ -n "$(git status --porcelain)" ]]; then
    echo "Error: working directory is not clean. Commit or stash your changes before releasing." >&2
    exit 1
fi

if git rev-parse "v${NEW_VERSION}" >/dev/null 2>&1; then
    echo "Error: tag v${NEW_VERSION} already exists." >&2
    exit 1
fi

echo "Bumping version references to ${NEW_VERSION}..."

echo -n "${NEW_VERSION}" > VERSION
echo -n "${NEW_VERSION}" > backend/VERSION

for package_json_file in frontend-openui/package.json frontend-admin/package.json; do
    python3 - "${package_json_file}" "${NEW_VERSION}" <<'PYEOF'
import json
import sys

file_path, new_version = sys.argv[1], sys.argv[2]
with open(file_path) as f:
    data = json.load(f)
data["version"] = new_version
with open(file_path, "w") as f:
    json.dump(data, f, indent=2)
    f.write("\n")
PYEOF
done

echo ""
echo "Version files updated. Now add an entry to CHANGELOG.md under a new"
echo "'## [${NEW_VERSION}] - $(date +%Y-%m-%d)' heading (move items out of [Unreleased])."
echo "Press Enter once you've done that, or Ctrl+C to abort without committing."
read -r

git add VERSION backend/VERSION frontend-openui/package.json frontend-admin/package.json CHANGELOG.md
git commit -m "Release v${NEW_VERSION}"
git tag -a "v${NEW_VERSION}" -m "Release v${NEW_VERSION}"

echo ""
echo "Done. Review with 'git show HEAD' and 'git show v${NEW_VERSION}', then push with:"
echo "    git push origin main --tags"
