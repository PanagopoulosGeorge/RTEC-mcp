# RTEC Syntax Reference

## Overview

The Event Calculus is a logic-based formalism for representing and reasoning about events and their effects. The Run-Time Event Calculus (RTEC) is a Prolog programming implementation of the Event Calculus, that has been optimised for composite activity recognition. Below, we summarise the language of RTEC. 

Following the Prolog convention, variables start with an upper-case letter, while predicates and constants start with a lower-case letter. Each rule ends with a full-stop “.”, while the head of a rule is separated from its body with “:-”. 

A fluent is a property that may have different values at different points in time. The term F=V denotes that fluent F has value V.  Boolean fluents are a special case in which the possible values are “true” and “false”. 

## RTEC predicates
Below are the predicates of RTEC.

RTEC - Predicate 1: happensAt(E,T)
Meaning: Event E occurs at time T

RTEC - Predicate 2: holdsAt(F=V,T)
Meaning: The value of fluent F is V at time T

RTEC - Predicate 3: holdsFor(F=V,I)
Meaning: I is the list of the maximal intervals during which F=V holds continuously

RTEC - Predicate 4: initiatedAt(F=V,T)
Meaning: At time T a period of time for which F=V is initiated

RTEC - Predicate 5: terminatedAt(F=V,T)
Meaning: At time T a period of time for which F=V is terminated

RTEC - Predicate 6: union_all(L,I)
Meaning: I is the list of maximal intervals produced by the union of the lists of maximal intervals of list L

RTEC - Predicate 7: intersect_all(L,I)
Meaning: I is the list of maximal intervals produced by the intersection of the lists of maximal intervals of list L

RTEC - Predicate 8: relative_complement_all(I',L,I)
Meaning: I is the list of maximal intervals produced by the relative complement of the list of maximal intervals I' with respect to every list of maximal intervals of list L

RTEC also includes two built-in events.

Built-in event 1: start(F=V)
Meaning: Event “start(F=V)” takes place at each starting point of each maximal interval of fluent-value pair F=V

Built-in event 2: end(F=V)
Meaning: Event “end(F=V)” takes place at each ending point of each maximal interval of fluent-value pair F=V


## Simple Fluents
There are two ways in which a composite activity may be defined in the language of RTEC. In the first case, a composite activity definition may be specified by means of rules with “initiatedAt(F=V, T)” or “terminatedAt(F=V, T)” in their head. This is called a simple fluent definition.

The first body literal of an “initiatedAt(F=V,T)” rule is a positive “happensAt” predicate; this predicate is followed by a possibly empty set of positive or negative “happensAt” and “holdsAt” predicates. Negative predicates are prefixed with “not” which expresses negation-by-failure. In some cases, the body of an “initiatedAt(F=V,T)” rule may include predicates expressing background knowledge. 

“terminatedAt(F=V,T)” rules are specified in a similar way.

**Rule body constraints (simple fluents):**
- Disjunction (`;`) is NOT permitted anywhere in the body of `initiatedAt` or `terminatedAt` rules.
- Each clause must start with a positive `happensAt` literal.
- Arithmetic comparisons (`>`, `<`, `=<`, `>=`, `=:=`, `=\=`) require both sides to be fully instantiated; call `thresholds/2` or other background predicates to bind threshold variables before using them in comparisons.

## Deadline Fluents (fi/3 and p/1)

Some simple fluents automatically transition from one value to another after a time deadline. This is expressed with two declarations:

```
fi(F=V1, F=V2, Duration)
p(F=V1)
```

- `fi(F=V1, F=V2, Duration)` — declares that if `F=V1` has been continuously true for `Duration` time units without explicit termination, RTEC automatically initiates `F=V2` (and terminates `F=V1`).
- `p(F=V1)` — declares that `F=V1` has persistence: the auto-initiation at the deadline reuses existing starting points without re-evaluating the `initiatedAt` body.

These are used together to express "the activity can hold for at most Duration time before automatically transitioning to a different value."

When using `fi/3`:
- You must also declare grounding for the target value
- The `=false` value is terminated automatically when `initiatedAt(F=true, T)` fires (inertia semantics reset all values at initiation time).

## Statically Defined Fluents
The second way in which a composite activity may be defined in the language of RTEC concerns statically determined fluents. In this case, a composite activity definition may be specified by means of a rule with “holdsFor(F=V, I)” in its head. The body of such a rule may include “holdsFor” conditions for fluents other than F, as well as some of the interval manipulation constructs of RTEC, i.e. “union_all”, “intersect_all”, and “relative_complement_all”. In some cases, a “holdsFor(F=V, I)” rule may include predicates expressing background knowledge. A rule with “holdsFor(F=V, I)” in the head is called a statically determined fluent definition.