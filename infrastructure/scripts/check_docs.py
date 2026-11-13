"""Documentation stays in step with the code.

    python3 infrastructure/scripts/check_docs.py      # prints what is out of step

The same checks run in the test suite (``tests/core/test_docs.py``), so every
full run fails when the documentation drifts from the code:

* ``path`` and ``path:symbol`` in living documents point to files and names
  that exist (renamed or removed code shows up at once);
* relative Markdown links and their ``#anchors`` resolve, in every document;
* every model is named in ``AS_IS_ARCHITECTURE.md``; every Celery task and
  beat entry in ``BACKGROUND_JOBS.md``; every management command somewhere
  in the documents; every ``module_*_enabled`` switch in ``FEATURE_MATRIX.md``
  or AS-IS; every bot handler module in ``BOT_RUNTIME.md``; every environment
  variable of ``core/settings/env.py`` in an ``.env`` example or a guide;
* the instructions for agents keep their shape: ``CLAUDE.md`` under 200 lines,
  every ``.claude/rules`` file has ``paths:`` that match files and is listed
  in ``CLAUDE.md``;
* the PQ table has five cells per row, the ADR range in ``AGENTS.md`` names
  the last ADR, and the number of tests agrees between ``TESTING.md`` and
  AS-IS.

What it cannot see is a description that is wrong in words; that is what the
"documents in the same commit" rule is for (``.claude/rules/docs.md``).
"""

from __future__ import annotations

import fnmatch
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Directories that are not part of the project's documentation or code.
SKIP_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".ruff_cache",
    ".mypy_cache", ".pytest_cache", "htmlcov", "media", "staticfiles",
    "brag-output", "Claude outputs",
}
# Dated records: their code references describe the code of their day.
# Links in them are still checked.
HISTORICAL = ("docs/audits/", "TABLEOS_TZ.md", "PROJECT_CHEATSHEET.md")
CLAUDE_MD_LIMIT = 200

FENCE = re.compile(r"^(```|~~~).*?^\1", re.S | re.M)
SPAN = re.compile(r"`([^`\n]+)`")
LINK = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)\)")
HEADING = re.compile(r"^#{1,6}\s+(.*?)\s*#*\s*$", re.M)
REPO_PATH = re.compile(r"^(?:\.{1,2}/)*[\w.\-]+(?:/[\w.\-]+)*/?$")
CODE_EXT = (".py", ".html", ".js", ".css", ".po", ".yml", ".yaml", ".toml",
            ".md", ".json", ".txt", ".sh", ".lock")


# --- files -------------------------------------------------------------------


def _gitignore_patterns() -> list[str]:
    path = ROOT / ".gitignore"
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    return [ln.strip().rstrip("/") for ln in lines if ln.strip() and not ln.startswith(("#", "!"))]


def _ignored(rel: str, patterns: list[str]) -> bool:
    parts = rel.rstrip("/").split("/")
    for pattern in patterns:
        if "/" in pattern:
            if fnmatch.fnmatch(rel, pattern) or rel.startswith(pattern + "/"):
                return True
        elif any(fnmatch.fnmatch(part, pattern) for part in parts):
            return True
    return False


def project_files() -> list[str]:
    """Repository-relative paths of the project's files (no local state)."""
    patterns = _gitignore_patterns()
    found = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        rel_dir = os.path.relpath(dirpath, ROOT)
        rel_dir = "" if rel_dir == "." else rel_dir.replace(os.sep, "/")
        dirnames[:] = [
            d for d in dirnames
            if d not in SKIP_DIRS and not _ignored(f"{rel_dir}/{d}".lstrip("/"), patterns)
        ]
        for name in filenames:
            rel = f"{rel_dir}/{name}".lstrip("/")
            if not _ignored(rel, patterns):
                found.append(rel)
    return sorted(found)


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8", errors="ignore")


def _without_fences(text: str) -> str:
    # Keep line numbers: replace fenced blocks by the same number of newlines.
    return FENCE.sub(lambda m: "\n" * m.group(0).count("\n"), text)


def markdown_files(files: list[str]) -> list[str]:
    return [f for f in files if f.endswith(".md")]


def living(rel: str) -> bool:
    return not rel.startswith(HISTORICAL)


# --- code references -----------------------------------------------------------


def _defines(text: str, name: str) -> bool:
    pattern = re.compile(
        (r"(?:def|class|async def)\s+{n}\b|^\s*{n}\s*[:=(]|\b{n}\s*="
         r"|[\"']{n}[\"']|\b{n}\s*\(|\.{n}\b").format(n=re.escape(name)),
        re.M,
    )
    return bool(pattern.search(text))


def code_references(files: list[str]) -> list[str]:
    fileset = set(files)
    dirs = {"/".join(f.split("/")[:i]) for f in files for i in range(1, f.count("/") + 1)}
    roots = {f.split("/")[0] for f in files}
    issues = []
    for doc in markdown_files(files):
        if not living(doc):
            continue
        text = _without_fences(_read(doc))
        base = os.path.dirname(doc)
        for match in SPAN.finditer(text):
            span = match.group(1).strip()
            after = text[match.end():match.end() + 3]
            if "/" not in span or after.lstrip().startswith("→") or any(c in span for c in "*{<… "):
                continue
            path, _, symbol = span.partition(":")
            if not REPO_PATH.match(path) or path.startswith("/"):
                continue
            if path.startswith(("./", "../")):
                rel = os.path.normpath(os.path.join(base, path)).replace(os.sep, "/")
                if rel.startswith(".."):
                    continue
            else:
                rel = path
            rel = rel.rstrip("/")
            first = rel.split("/")[0]
            if rel in fileset or rel in dirs:
                targets = [rel]
            elif first in roots:
                targets = []
            elif rel.endswith(CODE_EXT):
                # Short form such as `tables/services.py`: match by suffix.
                targets = [f for f in files if f.endswith("/" + rel)]
            else:
                continue  # not a repository path: Europe/Kyiv, image/webp, ...
            line = text.count("\n", 0, match.start()) + 1
            if not targets:
                if not _ignored(rel, _gitignore_patterns()):
                    issues.append(f"{doc}:{line}: нет файла `{path}`")
                continue
            if symbol and re.fullmatch(r"[A-Za-z_][\w.]*", symbol):
                name = symbol.split(".")[-1]
                readable = [t for t in targets if t in fileset]
                if readable and not any(_defines(_read(t), name) for t in readable):
                    issues.append(f"{doc}:{line}: в `{path}` нет `{name}`")
    return issues


# --- links ---------------------------------------------------------------------


def github_slug(heading: str) -> str:
    text = re.sub(r"<[^>]+>", "", heading).strip().lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def _anchors(rel: str) -> set[str]:
    seen: dict[str, int] = {}
    result = set()
    for heading in HEADING.findall(_without_fences(_read(rel))):
        slug = github_slug(heading)
        count = seen.get(slug, 0)
        result.add(slug if count == 0 else f"{slug}-{count}")
        seen[slug] = count + 1
    return result


def markdown_links(files: list[str]) -> list[str]:
    fileset = set(files)
    issues = []
    anchors: dict[str, set[str]] = {}
    for doc in markdown_files(files):
        text = _without_fences(_read(doc))
        for match in LINK.finditer(text):
            target = match.group(1)
            if re.match(r"^[a-z]+:", target):
                continue  # http, https, mailto
            path, _, anchor = target.partition("#")
            line = text.count("\n", 0, match.start()) + 1
            if path:
                rel = os.path.normpath(os.path.join(os.path.dirname(doc), path))
                rel = rel.replace(os.sep, "/")
                if not (ROOT / rel).exists():
                    issues.append(f"{doc}:{line}: ссылка на несуществующий `{path}`")
                    continue
            else:
                rel = doc
            if anchor and rel.endswith(".md") and rel in fileset:
                if rel not in anchors:
                    anchors[rel] = _anchors(rel)
                if anchor not in anchors[rel]:
                    issues.append(f"{doc}:{line}: в `{rel}` нет заголовка `#{anchor}`")
    return issues


# --- inventory (needs Django) ----------------------------------------------------


def inventory(files: list[str]) -> list[str]:
    from django.apps import apps
    from django.conf import settings

    docs = {f: _read(f) for f in markdown_files(files) if living(f)}
    as_is = docs.get("docs/architecture/AS_IS_ARCHITECTURE.md", "")
    jobs = docs.get("docs/architecture/BACKGROUND_JOBS.md", "")
    matrix = docs.get("docs/product/FEATURE_MATRIX.md", "")
    runtime = docs.get("docs/architecture/BOT_RUNTIME.md", "")
    everything = "\n".join(docs.values())
    issues = []

    project_models = [
        m for m in apps.get_models()
        if m.__module__.split(".")[0] in {"apps", "bot", "webapp", "core"}
    ]
    for model in project_models:
        if not re.search(rf"\b{model.__name__}\b", as_is):
            issues.append(f"модель {model.__name__} ({model.__module__}) "
                          "не описана в AS_IS_ARCHITECTURE.md")

    for entry in getattr(settings, "CELERY_BEAT_SCHEDULE", {}).values():
        if entry["task"] not in jobs:
            issues.append(f"задача beat {entry['task']} не описана в BACKGROUND_JOBS.md")

    from core.celery import app as celery_app

    celery_app.loader.import_default_modules()
    for name in sorted(celery_app.tasks):
        if not name.startswith("celery.") and name not in jobs:
            issues.append(f"задача Celery {name} не описана в BACKGROUND_JOBS.md")

    for rel in files:
        parts = rel.split("/")
        is_command = parts[-3:-1] == ["management", "commands"] and rel.endswith(".py")
        if len(parts) >= 4 and is_command:
            command = parts[-1][:-3]
            mentioned = re.search(rf"\b{re.escape(command)}\b", everything)
            if not command.startswith("_") and not mentioned:
                issues.append(f"команда manage.py {command} ({rel}) не упомянута в документах")

    switches = {
        field.name
        for model in project_models
        for field in model._meta.get_fields()
        if field.name.startswith("module_") and field.name.endswith("_enabled")
    }
    for switch in sorted(switches):
        if switch not in matrix and switch not in as_is:
            issues.append(f"модуль {switch} не описан в FEATURE_MATRIX.md "
                          "или AS_IS_ARCHITECTURE.md")

    for rel in files:
        if rel.startswith("bot/handlers/") and rel.endswith(".py") and rel.count("/") == 2:
            stem = rel.rsplit("/", 1)[1][:-3]
            if stem != "__init__" and stem not in runtime:
                issues.append(f"обработчик {rel} не описан в BOT_RUNTIME.md")

    env_schema = ROOT / "core/settings/env.py"
    if env_schema.exists():
        described = "\n".join(
            _read(f) for f in (".env.example", ".env.production.example",
                               "docs/engineering/DEVELOPMENT.md", "docs/engineering/DEPLOYMENT.md")
            if (ROOT / f).exists()
        )
        aliases = re.findall(r'validation_alias="([A-Z0-9_]+)"', _read("core/settings/env.py"))
        for name in sorted(set(aliases)):
            if not re.search(rf"\b{name}\b", described):
                issues.append(f"переменная окружения {name} не описана в .env.example, "
                              ".env.production.example, DEVELOPMENT.md или DEPLOYMENT.md")
    return issues


# --- instructions and structure ----------------------------------------------------


def _glob_regex(pattern: str) -> re.Pattern:
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif pattern.startswith("**", i):
            out, i = out + ".*", i + 2
        elif pattern[i] == "*":
            out, i = out + "[^/]*", i + 1
        elif pattern[i] == "?":
            out, i = out + "[^/]", i + 1
        elif pattern[i] == "{":
            end = pattern.index("}", i)
            out += "(?:" + "|".join(map(re.escape, pattern[i + 1:end].split(","))) + ")"
            i = end + 1
        else:
            out, i = out + re.escape(pattern[i]), i + 1
    return re.compile(out)


def _rule_paths(text: str) -> list[str] | None:
    if not text.startswith("---\n"):
        return None
    front = text[4:text.find("\n---", 4)]
    if not re.search(r"^paths:\s*$", front, re.M):
        return None
    return re.findall(r'^\s*-\s*"([^"]+)"\s*$', front, re.M)


def instructions(files: list[str]) -> list[str]:
    issues = []
    if (ROOT / "CLAUDE.md").exists():
        claude = _read("CLAUDE.md")
        loaded = re.sub(r"<!--.*?-->", "", claude, flags=re.S).strip()
        lines = loaded.count("\n") + 1
        if lines > CLAUDE_MD_LIMIT:
            issues.append(f"CLAUDE.md: {lines} строк в контексте, больше {CLAUDE_MD_LIMIT} — "
                          "вынести область в .claude/rules/ или процедуру в навык")
        rules = (f for f in files if f.startswith(".claude/rules/") and f.endswith(".md"))
        for rule in sorted(rules):
            name = rule.rsplit("/", 1)[1]
            paths = _rule_paths(_read(rule))
            if not paths:
                issues.append(f"{rule}: нет списка paths: во frontmatter")
                continue
            for pattern in paths:
                regex = _glob_regex(pattern)
                if not any(regex.fullmatch(f) for f in files):
                    issues.append(f"{rule}: шаблон {pattern!r} не совпадает ни с одним файлом")
            if f"`{name}`" not in claude:
                issues.append(f"{rule}: файла нет в таблице «Правила по областям кода» CLAUDE.md")
    return issues


def structure(files: list[str]) -> list[str]:
    issues = []
    pq = "docs/product/OPEN_PRODUCT_QUESTIONS.md"
    if pq in files:
        for number, line in enumerate(_read(pq).splitlines(), 1):
            if line.startswith("| PQ-"):
                cells = re.split(r"(?<!\\)\|", line)[1:-1]
                if len(cells) != 5:
                    issues.append(f"{pq}:{number}: в строке {len(cells)} ячеек вместо 5")

    adrs = sorted(f for f in files if re.fullmatch(r"docs/decisions/ADR-\d{4}\.md", f))
    if adrs and "AGENTS.md" in files:
        last = adrs[-1].rsplit("/", 1)[1]
        if f"`{last}`" not in _read("AGENTS.md"):
            issues.append(f"AGENTS.md: в перечне решений нет последнего {last}")

    testing = "docs/engineering/TESTING.md"
    as_is = "docs/architecture/AS_IS_ARCHITECTURE.md"
    if testing in files and as_is in files:
        text = _read(testing)
        header = re.search(r"На \d{4}-\d{2}-\d{2} тестов \*\*(\d+)\*\*", text)
        in_as_is = re.search(r"^- (\d+) тест", _read(as_is), re.M)
        journal = [
            ln for ln in text.splitlines()
            if ln.startswith("| 20") and re.search(r"\b\d{3,} тест", ln)
        ]
        counts = {
            "TESTING.md (шапка)": header.group(1) if header else None,
            "AS_IS_ARCHITECTURE.md": in_as_is.group(1) if in_as_is else None,
            "TESTING.md (последний прогон в журнале)":
                max(re.findall(r"\b(\d{3,}) тест", journal[-1]), key=int) if journal else None,
        }
        known = {k: v for k, v in counts.items() if v}
        if len(set(known.values())) > 1:
            parts = ", ".join(f"{k} — {v}" for k, v in known.items())
            issues.append(f"число тестов расходится: {parts}")
    return issues


# --- entry points ----------------------------------------------------------------


def problems(with_inventory: bool = True) -> list[str]:
    files = project_files()
    result = code_references(files) + markdown_links(files) + instructions(files) + structure(files)
    if with_inventory:
        result += inventory(files)
    return result


def main() -> int:
    sys.path.insert(0, str(ROOT))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings.local")
    os.environ.setdefault("DJANGO_USE_SQLITE", "true")
    import django

    django.setup()
    found = problems()
    for issue in found:
        print(issue)
    summary = f"расхождений — {len(found)}" if found else "расхождений нет"
    print(f"Документация: {summary}.")
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main())
