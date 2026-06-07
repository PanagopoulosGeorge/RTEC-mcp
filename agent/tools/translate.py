"""Stage 1: natural language → typed FluentSpec (grounded semantic parser).

Turns a free-form request like "a person is happy as long as they are rich or
at the pub" into a typed, signature-grounded `FluentSpec`, which the builder
agent (stage 2) then turns into Prolog rules.

The parser is given only the domain *signature* (events / fluents / entity
values from vocabulary.yaml) — never the target fluent's definition — so it
cannot leak the answer it is supposed to derive. Every symbol it emits is
validated against that signature before the spec is handed downstream.
"""

import json

from openai import OpenAI

from ..config import AgentConfig, PROMPTS_DIR
from ..core.schemas import FluentSpec, TranslationResult, Vocabulary
from .registry import get_vocabulary


def _bare(name: str) -> str:
    """'rich(Person)' -> 'rich'; 'win_lottery' -> 'win_lottery'."""
    return name.split("(", 1)[0].strip()


def _signature_text(vocab: Vocabulary) -> str:
    """Render the vocabulary as the signature block shown to the parser."""
    lines = ["Events (instantaneous):"]
    lines += [f"  - {e}" for e in vocab.events] or ["  (none)"]
    lines.append("Simple fluents (durative, set by events):")
    lines += [f"  - {f}" for f in vocab.simple_fluents] or ["  (none)"]
    lines.append("SD fluents (durative, derived from other fluents):")
    lines += [f"  - {f}" for f in vocab.sd_fluents] or ["  (none)"]
    # Entity values are optional — large domains (maritime) don't enumerate them.
    if vocab.entities:
        lines.append("Entity values:")
        for etype, vals in vocab.entities.items():
            lines.append(f"  - {etype}: {', '.join(vals)}")
    return "\n".join(lines)


def _domain_examples_text(vocab: Vocabulary) -> str:
    """Render domain-specific fluent descriptions for the parser prompt.

    Each entry in vocab.patterns is a fluent name → NL task description.
    Showing all of them gives the parser a complete picture of what every
    output fluent in this domain means, which helps it ground the current
    request against the right vocabulary symbols.

    Returns an empty string when the vocabulary has no patterns, so the
    {{DOMAIN_EXAMPLES}} placeholder simply disappears from the prompt.
    """
    if not vocab.patterns:
        return ""
    lines = [
        "## Fluent descriptions for this domain",
        "",
        "The following are the natural-language descriptions of every output",
        "fluent in this domain. They use ONLY the events, fluents, and entity",
        "values listed in the signature above — no other symbols exist.",
        "Read them to understand naming conventions and value types before",
        "mapping the user's request.",
        "",
    ]
    for fluent_name, description in vocab.patterns.items():
        lines.append(f"**{fluent_name}**")
        for dl in description.strip().splitlines():
            lines.append(f"  {dl}")
        lines.append("")
    return "\n".join(lines).rstrip()


def _is_prolog_var(s: str) -> bool:
    """Return True for Prolog-style variables: starts with uppercase or '_'."""
    return bool(s) and (s[0].isupper() or s.startswith("_"))


def _known(vocab: Vocabulary) -> tuple[set[str], set[str], set[str], set[str] | None]:
    """Return (event names, simple-fluent names, sd-fluent names, entity values | None).

    Entity values is None when vocab.entities is empty, which signals that value
    validation should be skipped (domain too large to enumerate, e.g. maritime).
    """
    events = {_bare(e) for e in vocab.events}
    simple = {_bare(f) for f in vocab.simple_fluents}
    sd = {_bare(f) for f in vocab.sd_fluents}
    if vocab.entities:
        values: set[str] | None = {v for vals in vocab.entities.values() for v in vals}
        values |= {"true", "false"}
    else:
        values = None  # skip value checking — entity domain not enumerated
    return events, simple, sd, values


def _validate(spec: FluentSpec, vocab: Vocabulary) -> TranslationResult:
    """Check every symbol the spec references exists in the signature.

    Fluent and event names are always validated. Concrete value strings (e.g.
    'pub', 'nearPorts') are only validated when vocab.entities is populated;
    if entities is absent the check is skipped with a soft warning so that
    large or dynamic domains (maritime) don't need to enumerate all values.
    """
    events, simple, sd, values = _known(vocab)
    errors: list[str] = []
    warnings: list[str] = []

    if values is None:
        warnings.append(
            "entity values not enumerated in vocabulary — concrete value "
            "strings will not be validated against the domain"
        )

    # The target may be a fluent we are about to create; missing is a soft note.
    if _bare(spec.target) not in (simple | sd):
        warnings.append(
            f"target fluent '{spec.target}' is not declared in the vocabulary"
        )

    if spec.kind == "sd_fluent":
        if not spec.definition or not spec.definition.operands:
            errors.append("sd_fluent has no definition/operands")
        else:
            for c in spec.definition.operands:
                if _bare(c.fluent) not in (simple | sd):
                    errors.append(f"unknown fluent referenced: '{c.fluent}'")
                # Prolog variables (uppercase / underscore) are always valid as
                # value placeholders (e.g. location(X)=Y, stopped(V)=_Status).
                if (
                    values is not None
                    and not _is_prolog_var(c.value)
                    and c.value not in values
                ):
                    errors.append(
                        f"value '{c.value}' for '{c.fluent}' is not a known "
                        "entity value (true/false or an entity domain member)"
                    )
    else:  # simple_fluent
        if not spec.initiated_by:
            errors.append("simple_fluent has no initiating event")
        for e in spec.initiated_by + spec.terminated_by:
            # built-in RTEC events start(F=V) / end(F=V) are always valid.
            bare = _bare(e.event)
            if bare not in events and bare not in ("start", "end"):
                errors.append(f"unknown event referenced: '{e.event}'")

    return TranslationResult(
        spec=spec,
        valid=not errors,
        errors=errors,
        warnings=warnings,
        brief=spec.to_brief(),
    )


def translate_request(
    app: str,
    request: str,
    config: AgentConfig | None = None,
) -> TranslationResult:
    """Parse a natural-language request into a grounded FluentSpec.

    Args:
        app: Application name (used to load the signature).
        request: Free-form natural-language request describing one fluent.
        config: Agent config (model/temperature); defaults to AgentConfig().

    Returns:
        A TranslationResult holding the spec, its grounding errors/warnings,
        and the rendered brief for the builder. If the LLM output cannot be
        parsed, `spec` is None and `errors` explains why.
    """
    config = config or AgentConfig()
    vocab = get_vocabulary(app)

    system = (PROMPTS_DIR / "translate_system.md").read_text()
    system = (
        system
        .replace("{{APP}}", app)
        .replace("{{SIGNATURE}}", _signature_text(vocab))
        .replace("{{DOMAIN_EXAMPLES}}", _domain_examples_text(vocab))
    )

    client = OpenAI()
    response = client.chat.completions.create(
        model=config.model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": request},
        ],
        response_format={"type": "json_object"},
        temperature=0.0,
        max_tokens=config.max_tokens,
    )
    content = response.choices[0].message.content or "{}"

    try:
        spec = FluentSpec(**json.loads(content))
    except Exception as e:  # JSON or schema failure -> report, don't crash
        return TranslationResult(
            valid=False,
            errors=[f"could not parse spec from model output: {e}"],
        )

    return _validate(spec, vocab)
