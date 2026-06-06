% Expert rules for the toy domain
% This is the ground truth that the agent should learn to reproduce

% ============= SIMPLE FLUENTS =============

% rich(X)=true: initiated by win_lottery, terminated by lose_wallet
initiatedAt(rich(X)=true, T) :-
    happensAt(win_lottery(X), T).

terminatedAt(rich(X)=true, T) :-
    happensAt(lose_wallet(X), T).

% location(X)=Y: changes when go_to happens
initiatedAt(location(X)=Y, T) :-
    happensAt(go_to(X, Y), T).

% ============= SD FLUENTS =============

% happy(X)=true: union of being rich OR being at pub
holdsFor(happy(X)=true, I) :-
    holdsFor(rich(X)=true, I1),
    holdsFor(location(X)=pub, I2),
    union_all([I1, I2], I).

% ============= GROUNDING =============

% Input entity grounding
grounding(go_to(Person, Place)) :- person(Person), place(Place).
grounding(lose_wallet(Person)) :- person(Person).
grounding(win_lottery(Person)) :- person(Person).

% Output entity grounding
grounding(location(Person)=pub) :- person(Person).
grounding(location(Person)=home) :- person(Person).
grounding(location(Person)=work) :- person(Person).
grounding(rich(Person)=true) :- person(Person).
grounding(happy(Person)=true) :- person(Person).

% Optional indexing
index(go_to(_, Place), Place).
