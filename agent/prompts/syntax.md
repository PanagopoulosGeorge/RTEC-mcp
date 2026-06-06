# RTEC Syntax Reference

## Overview

RTEC (Run-Time Event Calculus) is a logic programming framework for complex event recognition. It processes streams of timestamped events and recognizes complex patterns (fluents) that hold over time intervals.

## Entity Types

### Events (instantaneous)

Events are point-in-time occurrences from the input stream.

```prolog
% Declaration
event(event_name/arity).
inputEntity(event_name/arity).

% Usage in rules
happensAt(event_name(Arg1, Arg2, ...), T).
```

### Simple Fluents (with inertia)

Simple fluents have values that persist over time until explicitly changed. They follow the "law of inertia" — once initiated, a value holds until terminated.

```prolog
% Declaration
simpleFluent(fluent_name/arity).
outputEntity(fluent_name/arity).

% Initiation: fluent becomes Value at time T
initiatedAt(fluent_name(Args)=Value, T) :-
    happensAt(trigger_event(Args), T),
    <additional_conditions>.

% Termination: fluent stops being Value at time T
terminatedAt(fluent_name(Args)=Value, T) :-
    happensAt(end_event(Args), T),
    <additional_conditions>.

% Check current value at time T (in rule bodies)
holdsAt(fluent_name(Args)=Value, T).
```

### Statically-Determined Fluents (no inertia)

SD fluents are derived purely from interval operations on other fluents. They have no inertia — their value at any time is determined by the current state of their dependencies.

```prolog
% Declaration
sDFluent(fluent_name/arity).
outputEntity(fluent_name/arity).

% Definition via interval operations
holdsFor(fluent_name(Args)=Value, I) :-
    holdsFor(dependency1(Args)=Value1, I1),
    holdsFor(dependency2(Args)=Value2, I2),
    <interval_operation>([I1, I2], I).
```

**Important**: SD fluents are defined with `holdsFor`, NOT `holdsAt`. They use interval operations, not point-in-time checks.

## Interval Operations

| Operation | Syntax | Description |
|-----------|--------|-------------|
| Union | `union_all([I1, I2, ...], I)` | I = I1 ∪ I2 ∪ ... |
| Intersection | `intersect_all([I1, I2, ...], I)` | I = I1 ∩ I2 ∩ ... |
| Complement | `relative_complement_all(I1, [I2, ...], I)` | I = I1 - I2 - ... |
| Duration filter | `intDurGreater(I1, Duration, I)` | Intervals with duration > D |
| Duration filter | `intDurLess(I1, Duration, I)` | Intervals with duration < D |

## Deadlines (Force Initiation)

Deadlines automatically change fluent values after a specified time.

```prolog
% After Duration time units, if From still holds, initiate To
fi(fluent(Args)=From, fluent(Args)=To, Duration).
```

## Initial Values

```prolog
initially(fluent(Args)=Value).
```

## Start/End Events

RTEC generates synthetic events when fluent values change:

```prolog
% Triggered when fluent becomes Value
happensAt(start(fluent(Args)=Value), T).

% Triggered when fluent stops being Value
happensAt(end(fluent(Args)=Value), T).
```

These can be used in rule bodies to react to state changes.

## Grounding Declarations

Every fluent must have grounding declarations that specify valid instantiations:

```prolog
% For fluents
grounding(fluent_name(X)=value) :- domain_predicate(X).

% For events
grounding(event_name(X, Y)) :- domain1(X), domain2(Y).
```

## Indexing (Optional)

Indexing improves performance by grouping entities:

```prolog
index(event_name(X, Y), Y).
```

## Complete Example: Voting Status

```prolog
% Declarations
simpleFluent(status/1).
outputEntity(status/1).

% Initial value
initially(status(_M)=null).

% Deadlines
fi(status(M)=proposed, status(M)=null, 10).
fi(status(M)=voting, status(M)=voted, 10).

% Initiation rules
initiatedAt(status(M)=proposed, T) :-
    happensAt(propose(_P, M), T),
    holdsAt(status(M)=null, T).

initiatedAt(status(M)=voting, T) :-
    happensAt(second(_S, M), T),
    holdsAt(status(M)=proposed, T).

initiatedAt(status(M)=voted, T) :-
    happensAt(close_ballot(C, M), T),
    role_of(C, chair),
    holdsAt(status(M)=voting, T).

initiatedAt(status(M)=null, T) :-
    happensAt(declare(C, M, _), T),
    role_of(C, chair),
    holdsAt(status(M)=voted, T).

% Grounding
grounding(status(M)=null) :- motion(M).
grounding(status(M)=proposed) :- motion(M).
grounding(status(M)=voting) :- motion(M).
grounding(status(M)=voted) :- motion(M).
```

## Complete Example: SD Fluent (Power)

```prolog
% Declaration
sDFluent(pow/1).
outputEntity(pow/1).

% Power to propose exists when status is null
holdsFor(pow(propose(_P, M))=true, I) :-
    holdsFor(status(M)=null, I).

% Power to vote exists when status is voting
holdsFor(pow(vote(_V, M))=true, I) :-
    holdsFor(status(M)=voting, I).

% Grounding
grounding(pow(propose(P, M))=true) :- person(P), motion(M).
grounding(pow(vote(V, M))=true) :- person(V), motion(M).
```

## Common Patterns

### OR condition (union)
```prolog
holdsFor(active(X)=true, I) :-
    holdsFor(running(X)=true, I1),
    holdsFor(walking(X)=true, I2),
    union_all([I1, I2], I).
```

### AND condition (intersection)
```prolog
holdsFor(meeting(X,Y)=true, I) :-
    holdsFor(close(X,Y)=true, I1),
    holdsFor(facing(X,Y)=true, I2),
    intersect_all([I1, I2], I).
```

### NOT condition (complement)
```prolog
holdsFor(idle(X)=true, I) :-
    holdsFor(present(X)=true, I1),
    holdsFor(active(X)=true, I2),
    relative_complement_all(I1, [I2], I).
```

### Duration constraint
```prolog
holdsFor(loitering(X)=true, I) :-
    holdsFor(stopped(X)=true, I1),
    intDurGreater(I1, 1800, I).  % > 30 minutes
```
