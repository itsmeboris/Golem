#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Create a GitLab merge request via git push options (SSH access only, no token needed).

Required:
  --title TEXT           MR title

Optional:
  --target BRANCH        Target branch (default: main)
  --description TEXT     MR description body
  --draft                Create as draft MR
  --squash               Squash commits on merge
  --remove-source        Remove source branch after merge
  --label LABEL          Add label (repeatable)
  --assign USER          Assign to user (repeatable)
  --dry-run              Print the command without executing
  --help                 Show this help

Examples:
  $(basename "$0") --title "Add new feature"
  $(basename "$0") --title "Fix bug" --target develop --draft
  $(basename "$0") --title "Refactor" --label "refactor" --label "cleanup" --squash
EOF
    exit 0
}

die() { echo "ERROR: $*" >&2; exit 1; }

TITLE=""
TARGET="main"
DESCRIPTION=""
DRAFT=false
SQUASH=false
REMOVE_SOURCE=false
LABELS=()
ASSIGNEES=()
DRY_RUN=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --title)         TITLE="$2"; shift 2 ;;
        --target)        TARGET="$2"; shift 2 ;;
        --description)   DESCRIPTION="$2"; shift 2 ;;
        --draft)         DRAFT=true; shift ;;
        --squash)        SQUASH=true; shift ;;
        --remove-source) REMOVE_SOURCE=true; shift ;;
        --label)         LABELS+=("$2"); shift 2 ;;
        --assign)        ASSIGNEES+=("$2"); shift 2 ;;
        --dry-run)       DRY_RUN=true; shift ;;
        --help)          usage ;;
        *)               die "Unknown option: $1" ;;
    esac
done

[[ -z "$TITLE" ]] && die "--title is required"

git rev-parse --is-inside-work-tree &>/dev/null || die "Not inside a git repository"

SOURCE_BRANCH=$(git branch --show-current 2>/dev/null) || die "Not on a branch (detached HEAD?)"
[[ -z "$SOURCE_BRANCH" ]] && die "Not on a branch (detached HEAD?)"
[[ "$SOURCE_BRANCH" == "$TARGET" ]] && die "Source branch '$SOURCE_BRANCH' is the same as target '$TARGET'"

PUSH_OPTS=()
PUSH_OPTS+=(-o "merge_request.create")
PUSH_OPTS+=(-o "merge_request.target=$TARGET")
PUSH_OPTS+=(-o "merge_request.title=$TITLE")

if [[ -n "$DESCRIPTION" ]]; then
    SANITIZED_DESC="${DESCRIPTION//$'\n'/ }"
    SANITIZED_DESC="${SANITIZED_DESC//$'\r'/}"
    PUSH_OPTS+=(-o "merge_request.description=$SANITIZED_DESC")
fi

if "$DRAFT"; then
    PUSH_OPTS+=(-o "merge_request.draft")
fi

if "$SQUASH"; then
    PUSH_OPTS+=(-o "merge_request.squash")
fi

if "$REMOVE_SOURCE"; then
    PUSH_OPTS+=(-o "merge_request.remove_source_branch")
fi

for label in "${LABELS[@]}"; do
    PUSH_OPTS+=(-o "merge_request.label=$label")
done

for assignee in "${ASSIGNEES[@]}"; do
    PUSH_OPTS+=(-o "merge_request.assign=$assignee")
done

if "$DRY_RUN"; then
    echo "=== DRY RUN ==="
    echo "Source: $SOURCE_BRANCH"
    echo "Target: $TARGET"
    echo "Title:  $TITLE"
    [[ -n "$DESCRIPTION" ]] && echo "Desc:   $DESCRIPTION"
    "$DRAFT" && echo "Draft:  yes"
    "$SQUASH" && echo "Squash: yes"
    "$REMOVE_SOURCE" && echo "Remove source: yes"
    [[ ${#LABELS[@]} -gt 0 ]] && echo "Labels: ${LABELS[*]}"
    [[ ${#ASSIGNEES[@]} -gt 0 ]] && echo "Assign: ${ASSIGNEES[*]}"
    echo ""
    echo "Command:"
    echo "  git push -u origin HEAD ${PUSH_OPTS[*]}"
    exit 0
fi

TRACKING_BRANCH="origin/$SOURCE_BRANCH"
NEEDS_PUSH=true
if git rev-parse --verify "$TRACKING_BRANCH" &>/dev/null; then
    LOCAL_HEAD=$(git rev-parse HEAD)
    REMOTE_HEAD=$(git rev-parse "$TRACKING_BRANCH")
    if [[ "$LOCAL_HEAD" == "$REMOTE_HEAD" ]]; then
        NEEDS_PUSH=false
    fi
fi

echo "Pushing '$SOURCE_BRANCH' and creating MR → $TARGET"
echo ""

if ! "$NEEDS_PUSH"; then
    echo "Branch already up-to-date with remote. Creating empty commit to trigger MR..."
    git commit --allow-empty -m "Create merge request" --quiet
fi

OUTPUT=$(git push -u origin HEAD "${PUSH_OPTS[@]}" 2>&1)
echo "$OUTPUT"

if echo "$OUTPUT" | grep -q "Everything up-to-date"; then
    echo ""
    echo "WARNING: Push had no effect. MR may not have been created."
    echo "Check GitLab manually or try again after adding a commit."
fi
