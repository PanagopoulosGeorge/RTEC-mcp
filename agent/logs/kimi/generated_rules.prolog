collectIntervals(proximity(_,_)=true).
dynamicDomain(vessel(_Vessel)).
dynamicDomain(vpair(_Vessel1,_Vessel2)).
needsGrounding(_, _, _) :- fail.
buildFromPoints(_) :- fail.

% ── withinArea ──
initiatedAt(withinArea(Vessel, AreaType)=true, T) :-
    happensAt(entersArea(Vessel, Area), T),
    areaType(Area, AreaType).

terminatedAt(withinArea(Vessel, AreaType)=true, T) :-
    happensAt(leavesArea(Vessel, Area), T),
    areaType(Area, AreaType).

terminatedAt(withinArea(Vessel, _AreaType)=true, T) :-
    happensAt(gap_start(Vessel), T).

grounding(entersArea(V, Area)) :- vessel(V), areaType(Area).
grounding(leavesArea(V, Area)) :- vessel(V), areaType(Area).
grounding(gap_start(V)) :- vessel(V).
grounding(withinArea(V, AreaType)=true) :- vessel(V), areaType(AreaType).
index(withinArea(V, AreaType)=true, V).

% ── gap ──
initiatedAt(gap(Vessel)=nearPorts, T) :-
    happensAt(gap_start(Vessel), T),
    holdsAt(withinArea(Vessel, nearPorts)=true, T).

initiatedAt(gap(Vessel)=farFromPorts, T) :-
    happensAt(gap_start(Vessel), T),
    \+holdsAt(withinArea(Vessel, nearPorts)=true, T).

terminatedAt(gap(Vessel)=_Status, T) :-
    happensAt(gap_end(Vessel), T).

grounding(gap_start(V)) :- vessel(V).
grounding(gap_end(V)) :- vessel(V).
grounding(gap(V)=PortStatus) :- vessel(V), portStatus(PortStatus).
index(gap(V)=_, V).

% ── lowSpeed ──
initiatedAt(lowSpeed(Vessel)=true, T) :-
    happensAt(slow_motion_start(Vessel), T).

terminatedAt(lowSpeed(Vessel)=true, T) :-
    happensAt(slow_motion_end(Vessel), T).

terminatedAt(lowSpeed(Vessel)=true, T) :-
    happensAt(gap_start(Vessel), T).

grounding(slow_motion_start(V)) :- vessel(V).
grounding(slow_motion_end(V)) :- vessel(V).
grounding(lowSpeed(V)=true) :- vessel(V).
index(lowSpeed(V)=true, V).

% ── highSpeedNearCoast ──
initiatedAt(highSpeedNearCoast(Vessel)=true, T) :-
    happensAt(velocity(Vessel, Speed, _CourseOverGround, _TrueHeading), T),
    holdsAt(withinArea(Vessel, nearCoast)=true, T),
    thresholds(hcNearCoastMax, HcNearCoastMax),
    Speed > HcNearCoastMax.

terminatedAt(highSpeedNearCoast(Vessel)=true, T) :-
    happensAt(velocity(Vessel, Speed, _CourseOverGround, _TrueHeading), T),
    thresholds(hcNearCoastMax, HcNearCoastMax),
    Speed < HcNearCoastMax.

terminatedAt(highSpeedNearCoast(Vessel)=true, T) :-
    happensAt(leavesArea(Vessel, Area), T),
    areaType(Area, nearCoast).

terminatedAt(highSpeedNearCoast(Vessel)=true, T) :-
    happensAt(gap_start(Vessel), T).

grounding(velocity(V, _Speed, _CourseOverGround, _TrueHeading)) :- vessel(V).
grounding(highSpeedNearCoast(V)=true) :- vessel(V).
index(highSpeedNearCoast(V)=true, V).

% ── trawlSpeed ──
initiatedAt(trawlSpeed(Vessel)=true, T) :-
    happensAt(velocity(Vessel, Speed, _CourseOverGround, _TrueHeading), T),
    holdsAt(withinArea(Vessel, fishing)=true, T),
    vesselType(Vessel, fishing),
    thresholds(trawlspeedMin, TrawlspeedMin),
    thresholds(trawlspeedMax, TrawlspeedMax),
    inRange(Speed, TrawlspeedMin, TrawlspeedMax).

terminatedAt(trawlSpeed(Vessel)=true, T) :-
    happensAt(velocity(Vessel, Speed, _CourseOverGround, _TrueHeading), T),
    thresholds(trawlspeedMin, TrawlspeedMin),
    thresholds(trawlspeedMax, TrawlspeedMax),
    \+inRange(Speed, TrawlspeedMin, TrawlspeedMax).

terminatedAt(trawlSpeed(Vessel)=true, T) :-
    happensAt(velocity(Vessel, _Speed, _CourseOverGround, _TrueHeading), T),
    \+holdsAt(withinArea(Vessel, fishing)=true, T).

terminatedAt(trawlSpeed(Vessel)=true, T) :-
    happensAt(leavesArea(Vessel, Area), T),
    areaType(Area, fishing).

terminatedAt(trawlSpeed(Vessel)=true, T) :-
    happensAt(gap_start(Vessel), T).

grounding(trawlSpeed(V)=true) :- vessel(V).
index(trawlSpeed(V)=true, V).

% ── trawlingMovement ──
initiatedAt(trawlingMovement(Vessel)=true, T) :-
    happensAt(change_in_heading(Vessel), T),
    holdsAt(withinArea(Vessel, fishing)=true, T),
    vesselType(Vessel, fishing).

terminatedAt(trawlingMovement(Vessel)=true, T) :-
    happensAt(leavesArea(Vessel, Area), T),
    areaType(Area, fishing).

terminatedAt(trawlingMovement(Vessel)=true, T) :-
    happensAt(gap_start(Vessel), T).

grounding(change_in_heading(V)) :- vessel(V).
grounding(trawlingMovement(V)=true) :- vessel(V).
index(trawlingMovement(V)=true, V).

% ── tuggingSpeed ──
initiatedAt(tuggingSpeed(Vessel)=true, T) :-
    happensAt(velocity(Vessel, Speed, _CourseOverGround, _TrueHeading), T),
    thresholds(tuggingMin, TuggingMin),
    thresholds(tuggingMax, TuggingMax),
    inRange(Speed, TuggingMin, TuggingMax).

terminatedAt(tuggingSpeed(Vessel)=true, T) :-
    happensAt(velocity(Vessel, Speed, _CourseOverGround, _TrueHeading), T),
    thresholds(tuggingMin, TuggingMin),
    thresholds(tuggingMax, TuggingMax),
    \+inRange(Speed, TuggingMin, TuggingMax).

terminatedAt(tuggingSpeed(Vessel)=true, T) :-
    happensAt(gap_start(Vessel), T).

grounding(tuggingSpeed(V)=true) :- vessel(V).
index(tuggingSpeed(V)=true, V).

% ── sarSpeed ──
initiatedAt(sarSpeed(Vessel)=true, T) :-
    happensAt(velocity(Vessel, Speed, _CourseOverGround, _TrueHeading), T),
    vesselType(Vessel, sar),
    thresholds(sarMinSpeed, SarMinSpeed),
    Speed > SarMinSpeed.

terminatedAt(sarSpeed(Vessel)=true, T) :-
    happensAt(velocity(Vessel, Speed, _CourseOverGround, _TrueHeading), T),
    thresholds(sarMinSpeed, SarMinSpeed),
    Speed < SarMinSpeed.

terminatedAt(sarSpeed(Vessel)=true, T) :-
    happensAt(gap_start(Vessel), T).

grounding(sarSpeed(V)=true) :- vessel(V).
index(sarSpeed(V)=true, V).

% ── changingSpeed ──
initiatedAt(changingSpeed(Vessel)=true, T) :-
    happensAt(change_in_speed_start(Vessel), T).

terminatedAt(changingSpeed(Vessel)=true, T) :-
    happensAt(change_in_speed_end(Vessel), T).

terminatedAt(changingSpeed(Vessel)=true, T) :-
    happensAt(gap_start(Vessel), T).

grounding(change_in_speed_start(V)) :- vessel(V).
grounding(change_in_speed_end(V)) :- vessel(V).
grounding(changingSpeed(V)=true) :- vessel(V).
index(changingSpeed(V)=true, V).

% ── movingSpeed ──
initiatedAt(movingSpeed(Vessel)=below, T) :-
    happensAt(velocity(Vessel, Speed, _CourseOverGround, _TrueHeading), T),
    vesselType(Vessel, Type),
    typeSpeed(Type, Min, _Max, _Avg),
    Speed < Min.

initiatedAt(movingSpeed(Vessel)=normal, T) :-
    happensAt(velocity(Vessel, Speed, _CourseOverGround, _TrueHeading), T),
    vesselType(Vessel, Type),
    typeSpeed(Type, Min, Max, _Avg),
    inRange(Speed, Min, Max).

initiatedAt(movingSpeed(Vessel)=above, T) :-
    happensAt(velocity(Vessel, Speed, _CourseOverGround, _TrueHeading), T),
    vesselType(Vessel, Type),
    typeSpeed(Type, _Min, Max, _Avg),
    Speed > Max.

terminatedAt(movingSpeed(Vessel)=_Status, T) :-
    happensAt(velocity(Vessel, Speed, _CourseOverGround, _TrueHeading), T),
    thresholds(movingMin, MovingMin),
    Speed < MovingMin.

terminatedAt(movingSpeed(Vessel)=_Status, T) :-
    happensAt(gap_start(Vessel), T).

grounding(velocity(V, _Speed, _CourseOverGround, _TrueHeading)) :- vessel(V).
grounding(movingSpeed(V)=MovingStatus) :- vessel(V), movingStatus(MovingStatus).
index(movingSpeed(V)=_, V).

% ── stopped ──
initiatedAt(stopped(Vessel)=nearPorts, T) :-
    happensAt(stop_start(Vessel), T),
    holdsAt(withinArea(Vessel, nearPorts)=true, T).

initiatedAt(stopped(Vessel)=farFromPorts, T) :-
    happensAt(stop_start(Vessel), T),
    \+holdsAt(withinArea(Vessel, nearPorts)=true, T).

terminatedAt(stopped(Vessel)=_Status, T) :-
    happensAt(stop_end(Vessel), T).

terminatedAt(stopped(Vessel)=_Status, T) :-
    happensAt(start(gap(Vessel)=_GapStatus), T).

grounding(stop_start(V)) :- vessel(V).
grounding(stop_end(V)) :- vessel(V).
grounding(stopped(V)=PortStatus) :- vessel(V), portStatus(PortStatus).
index(stopped(V)=_, V).

% ── trawling ──
holdsFor(trawling(Vessel)=true, I) :-
    holdsFor(trawlSpeed(Vessel)=true, Its),
    thresholds(trawlingTime, TrawlingTime),
    intDurGreater(Its, TrawlingTime, I).

grounding(trawling(V)=true) :- vessel(V).
index(trawling(V)=true, V).

% ── anchoredOrMoored ──
holdsFor(anchoredOrMoored(Vessel)=true, I) :-
    thresholds(aOrMTime, AOrMTime),
    holdsFor(stopped(Vessel)=farFromPorts, Isf),
    intDurGreater(Isf, AOrMTime, IsfLong),
    holdsFor(withinArea(Vessel, anchorage)=true, Ianch),
    intersect_all([IsfLong, Ianch], I1),
    holdsFor(stopped(Vessel)=nearPorts, Isn),
    intDurGreater(Isn, AOrMTime, IsnLong),
    union_all([I1, IsnLong], I).

grounding(anchoredOrMoored(V)=true) :- vessel(V).
index(anchoredOrMoored(V)=true, V).

% ── tugging ──
holdsFor(tugging(Vessel1, Vessel2)=true, I) :-
    oneIsTug(Vessel1, Vessel2),
    holdsFor(proximity(Vessel1, Vessel2)=true, Ip),
    holdsFor(tuggingSpeed(Vessel1)=true, It1),
    holdsFor(tuggingSpeed(Vessel2)=true, It2),
    intersect_all([Ip, It1, It2], If), If \= [],
    thresholds(tuggingTime, TuggingTime),
    intDurGreater(If, TuggingTime, I).

grounding(tugging(V1, V2)=true) :- vpair(V1, V2).
index(tugging(V1, V2)=true, V1).

% ── sarMovement ──
initiatedAt(sarMovement(Vessel)=true, T) :-
    happensAt(change_in_speed_start(Vessel), T),
    vesselType(Vessel, sar).

initiatedAt(sarMovement(Vessel)=true, T) :-
    happensAt(change_in_heading(Vessel), T),
    vesselType(Vessel, sar).

terminatedAt(sarMovement(Vessel)=true, T) :-
    happensAt(gap_start(Vessel), T).

grounding(change_in_speed_start(V)) :- vessel(V).
grounding(change_in_heading(V)) :- vessel(V).
grounding(sarMovement(V)=true) :- vessel(V).
index(sarMovement(V)=true, V).
