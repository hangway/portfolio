# Document & Repo Audit Protocol (Fable Audit SOP)

<!-- Source: @Kayly.AI "FABLE 實戰" carousel · Systematized 2026-07-06 · v1.0-EN -->
<!-- Scope: Apply this protocol whenever the user asks to audit, review, diagnose, or reorganize any document, SOP, skill, system prompt, or repo structure. -->

## Non-negotiable principles

1. **Propose first, act after approval.** NEVER modify or rewrite any file before the plan is explicitly approved. Control stays with the human.
2. **Evidence, not feel.** Every finding must carry its reasoning and risk, ranked by impact. Whether and how much to change is the human's decision.
3. **Persist the standard.** After any review, consolidate the criteria used into a standalone reusable file, so any future model can execute against the same standard. Models get replaced; documents remain.

## The four methods (deep → light)

| # | Method | Purpose | Positioning |
|---|--------|---------|-------------|
| 1 | Freeze judgment into a blueprint | Solidify review criteria into a reusable checklist | Highest value |
| 2 | Full-repo restructure | Clear historical baggage: duplicate files, outdated instructions, conflicting rules | One-pass cleanup |
| 3 | Three-part deep review of one key document | Rigorous audit of core SOPs / frequent skills / long system prompts | Deep single-point |
| 4 | One-line quick diagnosis | Impact-ranked issue list within minutes | Lightest entry |

Execution paths (pick one):
- **Value-first:** 1 → 2 → 3 (freeze judgment, then restructure, then deep-fix)
- **Entry-first:** 4 → 3 → 2 → 1 (start with the lightest line, work back toward the deeper methods)

---

## Method 1 — Freeze judgment into a blueprint (highest value)

Rationale: consolidating review criteria into a checklist turns judgment into a long-term asset. Swap in any later model and execute against the same standard — quality does not disappear with the model.

Flow: `Review judgment → REVIEW CHECKLIST (standalone file) → any model executes against it`

Prompt template:

```
Consolidate the criteria you just used for this review into a reusable
review checklist. Write it as a standalone file so that any other model
can execute against the same standard.
```

Output location: Claude Code → write into CLAUDE.md (or a rules file it indexes) · Claude.ai → save as Project instructions.
Precondition: at least one review (Method 3 or 4) must have been completed first, so there are "criteria just used" to consolidate.

---

## Method 2 — Full-repo restructure

Rationale: the longer a folder lives, the heavier the baggage — duplicate files, outdated instructions, rules that fight each other. Instead of patching file by file, read everything and propose a complete reorganization.

Deliverable: a reorganization plan marking each item **merge / split / delete**, with reasoning and risk per item. Typical cleanup patterns: multi-version leftovers (`README.md` / `README copy.md` / `README_old.md` → one merged file), case-duplicates (`Button.tsx` / `button.tsx`), stray files moved into proper folders (`todo.md` → `tasks/`), junk deleted (`notes.txt`, `.DS_Store`).

Prompt template:

```
Read through the entire repo and propose a reorganization plan: which
files should be merged, split, or deleted, with reasoning and risks for
each. Do not modify any files before the plan is approved.
```

Entry: Claude Code → open the project directly · Claude.ai → upload the whole folder.

---

## Method 3 — Three-part deep review of one key document

Targets: core SOPs, frequently used skills, long system prompts — documents that deserve stricter treatment.

Three-part setup: (a) assign a senior role, (b) state explicit criteria, (c) require a plan first.

Review criteria:

| Criterion | Check |
|-----------|-------|
| Conflicting instructions | Do any instructions contradict each other? |
| Edge-case gaps | Are any edge cases missing? |
| Redundancy | Is anything redundant or duplicated? |

Prompt template:

```
You are a senior reviewer. Review this SOP against three criteria:
conflicting instructions, missing edge cases, and redundancy.
Propose a rewrite plan first; do not rewrite anything before approval.
```

Decision rights: report the basis behind every finding. Whether to change, and how much, is decided by the human.

---

## Method 4 — One-line quick diagnosis (lightest entry)

Rationale: paste any existing document into the conversation and get an impact-ranked issue list within minutes. Start from this one line, then work back toward the three deeper methods.

Prompt template:

```
What internal contradictions, duplications, and omissions does this
document contain? Rank them by impact. Do not rewrite anything yet.
```

Entry: Claude.ai → paste the document · Claude Code → specify the file path.

---

## Execution checklist

- [ ] Material selected (skill / SOP / repo / system prompt)
- [ ] Prompt includes "do not rewrite / do not modify before approval"
- [ ] Findings ranked by impact, each with reasoning and risk
- [ ] Human approval obtained before any change
- [ ] Review criteria persisted as a standalone file (CLAUDE.md / Project instructions)
- [ ] Verified: a different model executing the same standard produces consistent quality
