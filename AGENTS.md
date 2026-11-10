# AGENTS.md

## How agent codes

You write a well-engineered code, just like DHH (creator of Rails)
who just learned our tech stack (k8s, infra, python, etc.).
Your code is well-designed using SOLID, KISS and YAGNI principles.
When in doubt, YAGNI wins: a pattern needs a problem it solves here.

## Engineering standards

Production quality from the first version; a prototype may lack features, not structure.
Anything deferred is a task with a reason.

- **Standard tools for solved problems:** Alembic migrations, pydantic-settings, tenacity, ruff,
  a type checker. Hand-written replacements need a reason in the design.
- **Clear boundaries:** domain rules apart from I/O, ports and adapters, one composition root;
  typed models instead of dicts and strings; rules as tables or dispatch, not if/elif chains.
- **Schema:** versioned forward-only migrations in their own pipeline step, compatible with the
  version still running (expand, then contract); the service checks the revision at start.
- **Outside world:** timeouts, bounded retries, idempotent effects; all input is untrusted and
  validated, secrets never reach git or logs.
- **Operability:** structured logs with item ids, separate liveness and readiness, graceful
  shutdown, metrics for queues and runs.
- **Gates in CI:** lint, types, `shellcheck`, tests (unit for rules, integration on real
  dependencies), pinned non-root images. Non-obvious decisions go to `design.md` with the why.

## Docs

### Docs in two languages

- `README.md` is the source; `README_ru.md` is its translation. Change both in the same edit.

### Diagrams

- Never edit `docs/diagrams/*_ru.svg` or `example*.svg` by hand: they are generated. Use the
  `diagrams` skill (`.claude/skills/diagrams/SKILL.md`) for any diagram change.
