# Launch Owner Checklist

Manual actions that cannot be automated. Complete in order.

## Phase 1: Repo Polish (Week 1-2)

### GitHub Settings
- [ ] Enable GitHub Discussions → Settings → Features → Discussions
  - Categories: Q&A, Ideas, Show & Tell
- [ ] Add repository topics: `ai-agent`, `claude`, `automation`, `llm`, `task-automation`, `developer-tools`, `python`
- [ ] Set repository description: "An autonomous AI agent daemon — picks up tasks, executes them, validates the output, and commits results."
- [ ] Upload social preview image (1280x640px) → Settings → Social preview

### Release
- [ ] Create tag: `git tag v0.1.0 && git push origin v0.1.0`
- [ ] Create GitHub Release from tag → Releases → Draft → paste CHANGELOG.md content

### Issues
- [ ] Label 5-10 existing issues/TODOs as "good first issue"
- [ ] Create GitHub issues from P3/P4 TODO items that would make good first contributions

### GIFs (budget a full day)
- [ ] Set up clean terminal (80 cols, dark theme, no personal info)
- [ ] Run demo prompts 5-10 times each — track success rate (need 8/10+)
- [ ] Record with asciinema / screen recorder during successful runs
- [ ] Convert to GIF (agg or similar) — target <30s, <5MB each
- [ ] GIFs to produce:
  - [ ] `demo-run.gif` — `golem run -f prompts/add-logging.md` end-to-end
  - [ ] `demo-dashboard.gif` — dashboard showing a task in progress
  - [ ] `demo-status.gif` — `golem status` output
- [ ] Save to `assets/` and update README GIF placeholders

### Verification
- [ ] Verify `pip install -e .` works from clean clone
- [ ] Verify `pip install git+https://github.com/itsmeboris/golem.git` works
- [ ] Run all demo prompts once more after GIF recording to confirm reliability

## Phase 2: Soft Launch (Week 3-4+)

### Setup
- [ ] Create Bitly/Dub.co account
- [ ] Create per-channel tracking links:
  - [ ] `golem-reddit-claudeai` → repo URL
  - [ ] `golem-reddit-chatgptcoding` → repo URL
  - [ ] `golem-reddit-sideproject` → repo URL
  - [ ] `golem-hn` → repo URL
  - [ ] `golem-devto` → repo URL
- [ ] Create Dev.to account

### Week 3 — Reddit
- [ ] Post to r/ClaudeAI (use draft from `docs/outreach/reddit-claudeai.md`)
  - [ ] Replace [GIF placeholder] with actual GIF
  - [ ] Replace GitHub link with tracking link
- [ ] Wait 1-2 days, evaluate response
- [ ] Post to r/ChatGPTCoding (use draft from `docs/outreach/reddit-chatgptcoding.md`)
- [ ] Post to r/SideProject (use draft from `docs/outreach/reddit-sideproject.md`)
- [ ] Respond to every comment within 24 hours

### Week 3 Evaluation — GATE for Show HN
- [ ] Did at least one Reddit post get 10+ upvotes?
- [ ] Did anyone actually try it?
- [ ] Were there substantive comments/questions?
- [ ] Incorporate feedback into README/messaging
- [ ] If yes to above → proceed to Show HN. If no → diagnose and fix first.

### Week 4+ — HN + Dev.to (conditional on Reddit success)
- [ ] Finalize Show HN post (use draft from `docs/outreach/show-hn.md`)
- [ ] Post to Hacker News
- [ ] Write and publish Dev.to article (use outline from `docs/outreach/devto-article.md`)
- [ ] Share on Twitter/X (use template from `docs/outreach/social-templates.md`)
- [ ] Share on LinkedIn

## Phase 3: Sustain (Month 2-3)

- [ ] Set calendar reminder: respond to issues/PRs within 24h
- [ ] Set calendar reminder: monthly article cadence
- [ ] After 14 days, check Bitly analytics for channel attribution
- [ ] Replenish "good first issue" labels as they get picked up
- [ ] Revisit competitive positioning table based on actual questions received
- [ ] If users request Discord/community server repeatedly → create one
- [ ] When demo project is stable and launch wave has landed → consider PyPI publication
