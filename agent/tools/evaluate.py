"""Evaluation tools for comparing RTEC output to gold standard."""

import sys
from pathlib import Path

# Add scoring utilities to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "execution scripts" / "scoring"))

from ..config import APPS_DIR
from ..core.schemas import EvalReport, FluentScore, IntervalDiff, Recognition
from .execute import run_rtec, parse_recognitions


def generate_gold(app: str) -> str:
    """
    Generate gold standard intervals by running RTEC with expert rules.
    
    Args:
        app: Application name
        
    Returns:
        Status message
    """
    app_path = APPS_DIR / app
    if not app_path.exists():
        raise ValueError(f"Application '{app}' not found")
    
    # Run RTEC with expert rules
    recognitions = run_rtec(app, use_generated=False)
    
    # Write to gold file
    gold_file = app_path / "gold_intervals.txt"
    with open(gold_file, 'w') as f:
        for rec in recognitions:
            intervals_str = ",".join(f"({s},{e})" for s, e in rec.intervals)
            args_str = ",".join(rec.args)
            f.write(f"recognitions(predictions,{rec.fluent},[[{args_str}],{rec.value}],[{intervals_str}]).\n")
    
    return f"Generated gold intervals: {len(recognitions)} recognitions written to {gold_file}"


def temporal_intersection(intervals1: list[tuple[int, int]], 
                          intervals2: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Compute intersection of two interval lists."""
    result = []
    i, j = 0, 0
    
    while i < len(intervals1) and j < len(intervals2):
        s1, e1 = intervals1[i]
        s2, e2 = intervals2[j]
        
        # Compute intersection
        start = max(s1, s2)
        end = min(e1, e2)
        
        if start < end:
            result.append((start, end))
        
        # Advance the interval that ends first
        if e1 < e2:
            i += 1
        else:
            j += 1
    
    return result


def temporal_difference(intervals1: list[tuple[int, int]], 
                        intervals2: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Compute intervals1 - intervals2 (set difference)."""
    if not intervals1:
        return []
    if not intervals2:
        return list(intervals1)
    
    result = []
    
    for s1, e1 in intervals1:
        current_start = s1
        
        for s2, e2 in intervals2:
            if s2 >= e1:
                break
            if e2 <= s1:
                continue
            
            # Overlap exists
            if s2 > current_start:
                result.append((current_start, s2))
            current_start = max(current_start, e2)
        
        if current_start < e1:
            result.append((current_start, e1))
    
    return result


def get_timepoints(intervals: list[tuple[int, int]]) -> int:
    """Count total timepoints in intervals."""
    return sum(e - s for s, e in intervals)


def compare_to_gold(app: str, fluents: list[str] | None = None) -> EvalReport:
    """
    Compare current recognition results to gold standard.

    Args:
        app: Application name
        fluents: Optional list of fluent names to scope the comparison to
            (e.g. ["rich"]). When provided, only these fluents contribute to
            per_fluent, diffs, and the micro/macro F1 — so a request for one
            fluent converges as soon as that fluent is correct. When None,
            the whole event description is evaluated.

    Returns:
        EvalReport with F1 scores and interval differences
    """
    app_path = APPS_DIR / app
    gold_file = app_path / "gold_intervals.txt"
    
    if not gold_file.exists():
        raise ValueError(f"Gold intervals not found. Run generate_gold('{app}') first.")
    
    # Parse gold intervals
    gold_recognitions = parse_recognitions(gold_file)
    
    # Run RTEC with generated rules
    generated_recognitions = run_rtec(app, use_generated=True)
    
    # Group by (fluent, value)
    def group_by_fv(recs: list[Recognition]) -> dict:
        groups = {}
        for r in recs:
            key = (r.fluent, r.value)
            if key not in groups:
                groups[key] = {}
            args_key = tuple(r.args)
            if args_key not in groups[key]:
                groups[key][args_key] = []
            groups[key][args_key].extend(r.intervals)
        return groups
    
    gold_grouped = group_by_fv(gold_recognitions)
    gen_grouped = group_by_fv(generated_recognitions)
    
    # Compute scores per fluent-value pair
    per_fluent = []
    diffs = []
    total_tp = total_fp = total_fn = 0
    
    all_keys = set(gold_grouped.keys()) | set(gen_grouped.keys())

    if fluents:
        wanted = set(fluents)
        all_keys = {(f, v) for (f, v) in all_keys if f in wanted}

    for fluent, value in all_keys:
        tp = fp = fn = 0
        fp_intervals = []
        fn_intervals = []
        
        gold_args = gold_grouped.get((fluent, value), {})
        gen_args = gen_grouped.get((fluent, value), {})
        
        all_args = set(gold_args.keys()) | set(gen_args.keys())
        
        for args in all_args:
            gold_int = sorted(gold_args.get(args, []))
            gen_int = sorted(gen_args.get(args, []))
            
            # True positives: intersection
            intersection = temporal_intersection(gold_int, gen_int)
            tp += get_timepoints(intersection)
            
            # False positives: in generated but not gold
            fp_diff = temporal_difference(gen_int, gold_int)
            fp += get_timepoints(fp_diff)
            fp_intervals.extend(fp_diff)
            
            # False negatives: in gold but not generated
            fn_diff = temporal_difference(gold_int, gen_int)
            fn += get_timepoints(fn_diff)
            fn_intervals.extend(fn_diff)
        
        # Compute metrics
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
        
        per_fluent.append(FluentScore(
            fluent=fluent,
            value=value,
            tp=tp, fp=fp, fn=fn,
            precision=precision,
            recall=recall,
            f1=f1
        ))
        
        if fp_intervals or fn_intervals:
            diffs.append(IntervalDiff(
                fluent=fluent,
                value=value,
                false_positives=fp_intervals[:10],  # Limit for readability
                false_negatives=fn_intervals[:10]
            ))
        
        total_tp += tp
        total_fp += fp
        total_fn += fn
    
    # Compute micro and macro averages
    micro_precision = total_tp / (total_tp + total_fp) if (total_tp + total_fp) > 0 else 0.0
    micro_recall = total_tp / (total_tp + total_fn) if (total_tp + total_fn) > 0 else 0.0
    micro_f1 = 2 * micro_precision * micro_recall / (micro_precision + micro_recall) if (micro_precision + micro_recall) > 0 else 0.0
    
    f1_scores = [s.f1 for s in per_fluent if s.tp + s.fp + s.fn > 0]
    macro_f1 = sum(f1_scores) / len(f1_scores) if f1_scores else 0.0
    
    # Only surface fluents that still need fixing — perfect-scoring entries
    # add no actionable signal and bloat the model's context window.
    imperfect = [s for s in per_fluent if s.f1 < 1.0]

    return EvalReport(
        micro_f1=micro_f1,
        macro_f1=macro_f1,
        per_fluent=imperfect,
        diffs=diffs,
    )
