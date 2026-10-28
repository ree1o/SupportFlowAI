# SupportFlowAI repository instructions

These standing instructions apply to all future Codex development tasks in this
repository, including work in subdirectories. Follow the user's explicitly requested
scope and read any applicable nested instructions before editing.

## Scope and milestone boundaries

- Work only on an explicitly requested coding task or an approved milestone.
- Never start the next milestone automatically. Completing or pushing one milestone
  does not authorize the next one. M2 is authorized; M3 requires separate approval.
- Read `docs/architecture.md` and `docs/roadmap.md` when planning implementation.
- Preserve the modular monolith, FastAPI, synchronous SQLAlchemy, and PostgreSQL
  decisions unless the user approves a change. Add infrastructure only for a concrete
  requirement. Basic authorization and tenant isolation begin with identity/domain
  data; advanced PostgreSQL RLS belongs to the hardening release.

## Git naming and commit conventions

- Never mention Codex, ChatGPT, OpenAI, GPT, or any AI assistant or model in commit
  messages (subject or body), branch names, pull request titles, or release notes.
- Describe the actual technical changes, not the tools used to implement them.
- Write concise, professional, descriptive Conventional Commit messages using only
  `feat`, `fix`, `refactor`, `test`, `docs`, `chore`, or `ci`. Use `type: description`
  or `type(scope): description` with a relevant optional scope.
- For Git workflow configuration, use technical wording such as
  `chore: establish persistent Git workflow`.
- Never add AI-generated attribution, assistant/model bylines, or co-author trailers
  (including `Co-authored-by`) to commits.
- Apply these conventions to new work only. Do not rename, amend, or rewrite existing
  commits or other history to make earlier work conform.

## Standing authorization: verify, commit, and push

After completing each explicitly requested coding task or approved milestone, run
the following workflow automatically. The user has already authorized ordinary
descriptive commits and non-force pushes to the existing `origin` on the current
branch; do not ask for that authorization again. Respect platform access controls.

1. Inspect `git status --short --branch`, the current branch, existing staged changes,
   and `git remote -v` before editing. Preserve unrelated user changes. Do not create
   or switch branches automatically. If HEAD is detached, stop publication and report it.
2. Verify that `origin` still identifies the existing GitHub repository
   `ree1o/SupportFlowAI` (HTTPS or SSH), and verify the remote branch before publishing.
   Do not replace remotes or create a new repository to work around access failures.
3. Run the tests, linting, formatting, type checks, builds, and migration/integration
   checks relevant to the change. Use the current README and CI commands. A skipped
   required check is not a pass. Do not invent results or rely on CI that has not run.
4. Review the complete working diff and new files, run `git diff --check`, and check
   for secrets and excluded artifacts. Review dependency/lockfile changes when present.
   Do not print suspected credential values into tool output or logs.
5. Stage only explicit task-owned paths. Never use a blanket add that might include
   unrelated changes. Review `git diff --cached --check`, `git diff --cached --stat`,
   and the actual staged diff; scan staged contents and paths for secrets/artifacts.
6. Only when relevant verification and review pass, create a descriptive Conventional
   Commit following the naming and attribution rules above. Do not create empty
   commits. Never bypass hooks or checks to make a commit succeed.
7. Review the outgoing commit range so unrelated pre-existing commits are not silently
   published. Push the current branch to the existing `origin` with a normal push,
   for example `git push origin HEAD:refs/heads/<current-branch>`. Use the actual branch
   name, not a hard-coded `main`. Do not push all branches or tags.
8. Verify the push result and remote branch tip. Report the commit hash, branch,
   remote, checks/results, and any remaining limitations. Then stop at the approved
   scope boundary.

If relevant tests/checks fail, fix within the approved scope and rerun them. Do not
commit or push failing work. If required verification cannot run, a secret is found,
authentication fails, branch protection rejects a push, or a push is non-fast-forward,
report the specific blocker. Keep any already-created local commit and report its
hash when a push fails. Do not claim it was pushed. Do not reset, amend, rebase, merge,
force-push, or change protections to bypass a failure. A later user instruction can
explicitly change the requested workflow or approve a conflict-resolution task.

## Checks by change type

Use available project environments; do not install tools globally just for a check.
From `backend` on Windows, use the root virtual environment without activation:

```powershell
..\.venv\Scripts\python.exe -m pip check
..\.venv\Scripts\ruff.exe check .
..\.venv\Scripts\ruff.exe format --check .
..\.venv\Scripts\pytest.exe
```

Use the corresponding `.venv/bin/` commands on Linux. Database-related changes
require a dedicated disposable PostgreSQL database via `TEST_DATABASE_URL` and the
appropriate migration upgrade/check/rollback tests. Never run destructive migration
tests against a user's development, shared, or production database.

For frontend changes, run `npm.cmd ci` when dependencies change and `npm.cmd run build`
from `frontend` (use `npm` on Linux). Build includes strict TypeScript checks. Run
additional browser/tests when required by changed behavior. For Docker/CI changes,
validate Compose configuration and exercise the affected build/startup paths.

Documentation/instruction/ignore-only changes require content review, link/path checks
where applicable, whitespace checks, and ignore/secret checks; a full application or
database suite is not required solely for prose edits. Explain verification scope.
Keep these commands aligned with the project as its testing capabilities evolve.

## Secrets and generated files

- Never commit real `.env` files or variants, credentials, API keys, tokens, private
  keys, virtual environments, dependency directories, test databases, caches, coverage
  output, logs, or generated build artifacts. Keep `.gitignore` effective.
- `.env.example` is allowed only as a reviewed template containing placeholders or
  clearly disposable local-demo values. Dependency lockfiles are intentional source
  inputs and should be committed when dependencies change.
- Ignore rules do not remove files already tracked: inspect tracked/staged paths too.
  Do not force-add ignored files. Do not move credentials into source to bypass ignores.
- If a real secret is discovered, stop publication, report its location without its
  value, and request the necessary remediation. Never rewrite history as remediation.

## Prohibited Git operations

Never force-push (including `--force-with-lease`), rewrite published or local history,
amend existing commits, delete remote branches, create a new local/GitHub repository,
or change repository visibility/protections. Do not use destructive reset/clean commands
to discard user changes. Never start the next milestone automatically.
