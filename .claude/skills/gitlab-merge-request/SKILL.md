---
name: gitlab-merge-request
description: Create GitLab merge requests from the current branch. Use when asked to create MR, create merge request, push and create MR, or submit changes to GitLab. Uses git push options so only SSH access is needed, no API token required.
---

# GitLab Merge Request

Create merge requests on GitLab using [scripts/create_mr.sh](scripts/create_mr.sh).
Uses GitLab push options — only SSH access needed, no API token.

## When to Use

- User asks to "create a merge request", "create MR", "open MR"
- User asks to "push and create MR"
- User wants to submit current branch changes for review on GitLab

## Prerequisites

- SSH access to the GitLab remote (the same access used for `git push`)
- Current directory is inside a git repo with a GitLab remote
- Must be on a feature branch (not the target branch)

## Step 1: Gather Context

Run in parallel to understand what will go into the MR:

```bash
git log --oneline main..HEAD
git diff --stat main..HEAD
```

From the output, determine:
- **Title**: First commit subject if single commit, otherwise summarize
- **Description**: List of commits and a brief summary of what changed

Ask the user if the title/description looks right before proceeding.

## Step 2: Run the Script

IMPORTANT: `--description` must be a single line (no newlines). The script sanitizes
newlines automatically, but keep descriptions concise for best results.

```bash
SKILL_DIR="<path-to-cursor-skills>/skills/gitlab-merge-request"

${SKILL_DIR}/scripts/create_mr.sh \
  --title "Your MR title" \
  --description "Description of changes"
```

### Common Options

| Flag | Purpose | Example |
|------|---------|---------|
| `--target BRANCH` | Target branch (default: main) | `--target develop` |
| `--draft` | Create as draft MR | `--draft` |
| `--squash` | Squash commits on merge | `--squash` |
| `--remove-source` | Delete branch after merge | `--remove-source` |
| `--label LABEL` | Add label (repeatable) | `--label "bugfix"` |
| `--assign USER` | Assign to user (repeatable) | `--assign bsobol` |
| `--dry-run` | Preview without pushing | `--dry-run` |

Run `scripts/create_mr.sh --help` for full usage.

## Step 3: Report Result

GitLab prints the MR URL in the push output. Show it to the user.

If the push fails:
- **"rejected"** → Branch may need rebase, help the user resolve
- **"Permission denied"** → SSH key not configured for this GitLab instance

## Example

User: "Create a merge request for my changes"

```bash
$ git log --oneline main..HEAD
a1b2c3d Add gitlab-merge-request skill

$ /path/to/skills/gitlab-merge-request/scripts/create_mr.sh \
    --title "Add gitlab-merge-request skill" \
    --description "New skill for creating GitLab MRs from Cursor via push options." \
    --remove-source

# Output:
# Pushing 'user/add-gitlab-skill' and creating MR → main
#
# remote: View merge request for user/add-gitlab-skill:
# remote:   https://gitlab.example.com/project/repo/-/merge_requests/3
```
