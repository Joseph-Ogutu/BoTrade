# Pre-push security scan

Scan time: 2026-10-31T22:48:52Z (UTC). Scope: every commit on every ref of THIS repository (27 commits at scan time, one branch `main`), the working tree, and commit metadata. Tool: `scripts/scan_history.py` (prints pattern names, commit hashes and paths only, never matched text; its tests plant a fake secret and prove it is not printed). No push was done and no remote is configured.

Limits: the scan is regex based. The format of a new-API Deriv PAT is not known (api-notes), so a token that matches none of the patterns and is not assigned to a secret-looking name would not be found. After the first real PAT exists, add a maintained scanner (for example gitleaks or detect-secrets) as a pre-commit hook; neither is installed yet.

## 1. Result in one line

No private keys, cloud or Git host tokens, JWTs, Bearer tokens, PAT-prefixed strings, credentials in URLs (apart from one documented placeholder) or long literals assigned to secret-looking names were found in any commit. No token file and no real env file was ever committed.

## 2. Findings (paths and hashes only)

| Check | Result |
|---|---|
| Env files in history | Only `.env.example` (first in `c399a69`). It holds empty values and a `CHANGE_ME` database password placeholder (line 13). |
| Token or secret named files in history | None. |
| Database or data files in history | None (`data/` and `*.db`, `*.sqlite*` are git-ignored; holdout data is not in git). |
| Agent or editor state (`.claude`, `.vscode`, `.idea`) in history | None. |
| Key-format patterns (private key block, AWS, GitHub, Slack, Anthropic, OpenAI-style, JWT, `pat_`, Bearer literal) | 0 paths for each. |
| `otp=` literal in a URL | 2 paths, both non-secret: `schemas/deriv/websocket_response.schema.json` line 13 (Deriv's own documentation example `otp=abc123xyz789`, first in `fb56cd7`) and `tests/integration/test_public_stream.py` line 101 (a fake value used to prove URLs are redacted, first in `5d51c98`). |
| URL with embedded credentials | 2 paths, both the documented placeholder `postgresql+asyncpg://bot:CHANGE_ME@postgres...`: `.env.example` and `docs/HANDBOOK.md` (first in `19af90c`). |
| Long literal assigned to a secret-like name | 0 paths. |
| E-mail addresses in file contents | 0 paths. |
| Absolute home paths in file contents | 1 path, `docs/runbook.md` (first in `86c65ec`): the generic VM paths `/home/deploy/...`. No `/home/joseph-ogutu` path appears in any tracked file. |
| Old token file NAME mentioned in text | The file name `Ogutu Bot API TOKEN pat.txt` appears as plain text (the file's contents were never read or copied) in `docs/existing-bot-audit.md` (6 lines) and `STATE.md` (1 line), in commits including `fb56cd7`, `f491b31`, `f11b872`, `f0d24e5` and `eeca178` and in the earlier ones that contain those files. It reveals your name and that a token file exists, not any secret. |
| Commit metadata | All 27 commits: author and committer `Joseph-Ogutu <ogutujoseph892@gmail.com>`. A push publishes that e-mail address. |
| `legacy/` | Not in the repository: no tracked path, no path in any commit, and no `legacy*` directory in the working tree. The old bot lives in a separate directory outside this repo with its own git history and GitHub remote; none of it is in this repo, only the read-only audit text. |
| Ignored files present in the working tree | `.venv/`, `data/`, `.pytest_cache/`, `__pycache__/`. All git-ignored, so a normal push excludes them. There is no `.env` file here. |

## 3. What a push would include

- Branch `main` only. No remote is configured, so nothing can be pushed until you run `git remote add origin ...`.
- 27 commits at scan time, plus the commit that adds this report.
- 147 tracked files before this report: schemas 50 (the vendored Deriv schemas, about 0.5 MB, the largest being `rest-api-openapi.json` at 241 KB), src 29, docs 22, tests 21, scripts 11, root files 11, deploy 2, config 1.
- Content categories: source and tests; the HANDBOOK (your planning document); `docs/existing-bot-audit.md` (a detailed review of your old bot, including how its risk controls fail); the kill criterion and amendments with the disclosure of what was observed; raw public Deriv API responses under `docs/evidence/`; the Dockerfile, compose file, runbook and systemd units; no collected tick data.
- Not included: `data/` (ticks, backfill, heartbeat), `.venv/`, caches, any `.env`.

## 4. Decisions for you before the first push (nothing was changed)

1. **Public or private repository.** The HANDBOOK, the old-bot audit and the pre-registration are fine as a private repo. Making it public would also publish how your previous bot failed, your name in the token file mention and your e-mail. A private repo still gives the external timestamp that the kill criterion wants.
2. **Commit e-mail.** To avoid publishing `ogutujoseph892@gmail.com`, set a GitHub no-reply address and rewrite author and committer on all commits before the first push. Rewriting changes every commit hash, so `docs/gates.md`, `docs/kill-criterion.md` and `STATE.md` reference hashes (`f0d24e5`, `eeca178`, `87f96c1`) that would no longer match, and the holdout start H0 (epoch 1793486149) is derived from the committer timestamp, which a rewrite must preserve exactly. This is why I did not do it without you. If you want it, do it once, now, before pushing; I can then update every hash reference and re-verify `scripts/holdout_start.py` gives the same H0.
3. **Old token file name.** Leave it, or replace it with a neutral phrase in the two files. Replacing in the latest commit does not remove it from history; removing it from history also rewrites hashes (same caveat as item 2). Do items 2 and 3 together or not at all.
4. **Push order.** Pushing the amendment commits promptly gives the external timestamp for H0. A rewrite before the first push keeps that clean.
