"""Read-only inspection tools for the QA agent.

These never compile or mutate an app — they only read existing rules and
run RTEC over them so the QA agent can ground its answers in real behavior.
"""

from ..config import APPS_DIR


# Which rules file each source maps to, in preference order.
_SOURCE_FILES = {
    "expert": ["expert_rules.prolog"],
    "generated": ["generated_rules.prolog"],
}


def read_rules(app: str, source: str = "expert") -> str:
    """Return the text of an app's rules file.

    Args:
        app: Application name.
        source: 'expert' (ground-truth rules) or 'generated' (agent output).

    Returns:
        The Prolog source as a string, prefixed with the file name.
    """
    app_path = APPS_DIR / app
    if not app_path.exists():
        raise ValueError(f"Application '{app}' not found in {APPS_DIR}")

    candidates = _SOURCE_FILES.get(source)
    if candidates is None:
        raise ValueError(
            f"Unknown source '{source}'. Use one of: {', '.join(_SOURCE_FILES)}"
        )

    for name in candidates:
        path = app_path / name
        if path.exists():
            return f"% {name}\n{path.read_text()}"

    raise ValueError(
        f"No {source} rules found for '{app}' (looked for {candidates})"
    )
