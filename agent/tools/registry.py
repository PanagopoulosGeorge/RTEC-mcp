"""App registry and vocabulary tools."""

import yaml
from pathlib import Path

from ..config import APPS_DIR, PROMPTS_DIR, AppConfig
from ..core.schemas import Vocabulary


def list_apps() -> list[str]:
    """List all registered applications."""
    if not APPS_DIR.exists():
        return []
    return [d.name for d in APPS_DIR.iterdir() if d.is_dir()]


def load_app(name: str) -> AppConfig:
    """Load application configuration."""
    app_path = APPS_DIR / name
    if not app_path.exists():
        raise ValueError(f"Application '{name}' not found")
    
    config_file = app_path / "config.yaml"
    if config_file.exists():
        with open(config_file) as f:
            data = yaml.safe_load(f)
        return AppConfig(name=name, path=app_path, **data)
    else:
        return AppConfig(name=name, path=app_path)


def get_vocabulary(app: str) -> Vocabulary:
    """
    Get the vocabulary (events, fluents, entities) for an application.
    
    Args:
        app: Application name
        
    Returns:
        Vocabulary object
    """
    app_path = APPS_DIR / app
    vocab_file = app_path / "vocabulary.yaml"
    
    if vocab_file.exists():
        with open(vocab_file) as f:
            data = yaml.safe_load(f)
        return Vocabulary(**data)
    
    # Try to extract from expert rules if vocabulary.yaml doesn't exist
    expert_rules = app_path / "expert_rules.prolog"
    if expert_rules.exists():
        return _extract_vocabulary_from_rules(expert_rules)
    
    return Vocabulary()


def _extract_vocabulary_from_rules(rules_file: Path) -> Vocabulary:
    """Extract vocabulary by parsing Prolog rules."""
    import re
    
    events = set()
    simple_fluents = set()
    sd_fluents = set()
    entities = {}
    
    with open(rules_file) as f:
        content = f.read()
    
    # Extract events from happensAt
    for match in re.finditer(r'happensAt\((\w+)\(', content):
        events.add(match.group(1))
    
    # Extract simple fluents from initiatedAt/terminatedAt
    for match in re.finditer(r'initiatedAt\((\w+)\(', content):
        simple_fluents.add(match.group(1))
    for match in re.finditer(r'terminatedAt\((\w+)\(', content):
        simple_fluents.add(match.group(1))
    
    # Extract SD fluents from holdsFor (definitions, not uses)
    for match in re.finditer(r'^holdsFor\((\w+)\(', content, re.MULTILINE):
        sd_fluents.add(match.group(1))
    
    # Extract entities from grounding/1
    for match in re.finditer(r'grounding\(.*\) :- (\w+)\((\w+)\)', content):
        entity_type = match.group(1)
        if entity_type not in entities:
            entities[entity_type] = []
    
    # Remove simple fluents from SD fluents (if defined with both)
    sd_fluents -= simple_fluents
    
    return Vocabulary(
        events=sorted(events),
        simple_fluents=sorted(simple_fluents),
        sd_fluents=sorted(sd_fluents),
        entities=entities
    )


def get_syntax_docs() -> str:
    """
    Get RTEC syntax documentation.
    
    Returns:
        Markdown string with syntax reference and examples
    """
    syntax_file = PROMPTS_DIR / "syntax.md"
    if syntax_file.exists():
        return syntax_file.read_text()
    
    # Return embedded default if file doesn't exist
    return """# RTEC Syntax Reference

## Entity Types

### Events (instantaneous)
```prolog
event(event_name/arity).
happensAt(event_name(Args), T).
```

### Simple Fluents (with inertia)
```prolog
simpleFluent(fluent_name/arity).

% Initiation: fluent becomes Value at time T
initiatedAt(fluent(Args)=Value, T) :-
    happensAt(some_event(Args), T),
    <conditions>.

% Termination: fluent stops being Value at time T  
terminatedAt(fluent(Args)=Value, T) :-
    happensAt(some_event(Args), T),
    <conditions>.
```

### Statically-Determined Fluents (no inertia)
```prolog
sDFluent(fluent_name/arity).

% Defined via interval operations
holdsFor(fluent(Args)=Value, I) :-
    holdsFor(other_fluent(Args)=Value, I1),
    holdsFor(another_fluent(Args)=Value, I2),
    intersect_all([I1, I2], I).  % or union_all, relative_complement_all
```

## Interval Operations

| Operation | Description |
|-----------|-------------|
| `union_all([I1,I2,...], I)` | I = I1 ∪ I2 ∪ ... |
| `intersect_all([I1,I2,...], I)` | I = I1 ∩ I2 ∩ ... |
| `relative_complement_all(I1, [I2,...], I)` | I = I1 - I2 - ... |
| `intDurGreater(I, D, IOut)` | Intervals where duration > D |

## Declarations

```prolog
% Input vs output entities
inputEntity(event_name/arity).
outputEntity(fluent_name/arity).

% Grounding (instantiation)
grounding(fluent(X)=value) :- domain_predicate(X).
grounding(event(X, Y)) :- domain1(X), domain2(Y).

% Indexing (optional, for efficiency)
index(event(X, Y), Y).
```

## Example: Rich Person

```prolog
% Events
event(win_lottery/1).
event(lose_wallet/1).

% Simple fluent: rich
simpleFluent(rich/1).

initiatedAt(rich(X)=true, T) :-
    happensAt(win_lottery(X), T).

terminatedAt(rich(X)=true, T) :-
    happensAt(lose_wallet(X), T).

% Grounding
grounding(rich(X)=true) :- person(X).
```
"""
