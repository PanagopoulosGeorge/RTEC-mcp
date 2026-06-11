"""Compile RTEC rules."""

import re
import subprocess
import tempfile
from pathlib import Path

from ..config import RTEC_COMPILER, APPS_DIR
from ..core.schemas import CompileResult


def _check_singleton_arithmetic(rules: str) -> list[str]:
    """Detect singleton variables used in arithmetic comparisons.

    A common agent mistake: bind a threshold variable in initiatedAt but
    forget to rebind it in the terminatedAt clause, then use it in a
    comparison like `Speed < TrawlspeedMin` where TrawlspeedMin is unbound.
    SWI-Prolog only warns about singletons; RTEC crashes at runtime with
    '</2: Arguments are not sufficiently instantiated'.

    Heuristic: within a clause body (lines between `:-` and `.`), find any
    uppercase-starting variable that (a) appears in an arithmetic comparison
    AND (b) is never the output of a binding call (thresholds/2, typeSpeed/4,
    vesselType/2, intDurGreater/3, holdsAt/2 etc.) earlier in the same clause.
    """
    errors = []
    arith_ops = re.compile(r'(?<![<>!])([<>]=?|=:=|=\\=)')
    binding_call = re.compile(
        r'\b(thresholds|typeSpeed|vesselType|areaType|inRange|holdsAt)\s*\('
    )
    var_pat = re.compile(r'\b([A-Z][A-Za-z0-9_]*)\b')

    # Split into clauses at top-level '.'
    clause_lines: list[tuple[int, str]] = []
    current: list[tuple[int, str]] = []
    for i, raw in enumerate(rules.splitlines(), 1):
        stripped = raw.strip()
        if stripped.startswith('%'):
            continue
        current.append((i, stripped))
        if stripped.endswith('.') and ':-' in ''.join(l for _, l in current):
            clause_lines.append(current)
            current = []
    if current:
        clause_lines.append(current)

    for clause in clause_lines:
        text = ' '.join(l for _, l in clause)
        if ':-' not in text:
            continue
        body = text.split(':-', 1)[1]

        # Collect variables bound by known binding predicates in this clause
        bound: set[str] = set()
        for m in binding_call.finditer(body):
            # The first argument after the predicate call is usually the key;
            # the second is the bound output variable.  Grab all uppercase vars
            # in the entire call expression as conservatively bound.
            start = m.end() - 1  # position of '('
            depth, j = 1, start + 1
            while j < len(body) and depth > 0:
                if body[j] == '(':
                    depth += 1
                elif body[j] == ')':
                    depth -= 1
                j += 1
            call_text = body[start:j]
            for v in var_pat.findall(call_text):
                bound.add(v)

        # Also treat variables bound by happensAt as bound
        for m in re.finditer(r'happensAt\s*\(', body):
            start = m.end() - 1
            depth, j = 1, start + 1
            while j < len(body) and depth > 0:
                if body[j] == '(':
                    depth += 1
                elif body[j] == ')':
                    depth -= 1
                j += 1
            call_text = body[start:j]
            for v in var_pat.findall(call_text):
                bound.add(v)

        # Find arithmetic comparisons and check each operand
        for m in arith_ops.finditer(body):
            # Extract left and right tokens around the operator
            left_chunk = body[:m.start()].rsplit(',', 1)[-1].rsplit('(', 1)[-1].strip()
            right_chunk = body[m.end():].split(',', 1)[0].split(')', 1)[0].strip()
            for token in (left_chunk, right_chunk):
                for var in var_pat.findall(token):
                    if var not in bound:
                        lineno = clause[0][0]
                        errors.append(
                            f"RTEC error (near line {lineno}): variable '{var}' "
                            f"used in arithmetic but not bound in this clause. "
                            f"Call thresholds(key, {var}) before the comparison. "
                            f"(Variables bound in other clauses do not carry over.)"
                        )
    return errors


def _check_no_semicolon(rules: str) -> list[str]:
    """Return error messages if any rule body contains a bare semicolon.

    RTEC's compiler does not handle `;` (disjunction) inside initiatedAt /
    terminatedAt / holdsFor bodies — it strips parentheses and the second
    branch ends up with unbound variables, causing a runtime crash. The fix
    is always to write two separate clauses, one per alternative.
    """
    errors = []
    for i, line in enumerate(rules.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith('%'):
            continue
        if ';' in stripped:
            errors.append(
                f"RTEC syntax error (line {i}): disjunction ';' is not "
                f"permitted in rule bodies."
                f"Offending line: {stripped!r}"
            )
    return errors


def compile_rules(app: str, rules: str) -> CompileResult:
    """
    Compile RTEC rules and check for syntax errors.
    
    Args:
        app: Application name
        rules: Prolog rules as a string
        
    Returns:
        CompileResult with success status and any errors
    """
    app_path = APPS_DIR / app
    if not app_path.exists():
        return CompileResult(
            success=False,
            errors=[f"Application '{app}' not found in {APPS_DIR}"]
        )
    
    # Write rules to temporary file
    with tempfile.NamedTemporaryFile(
        mode='w', 
        suffix='.prolog',
        dir=app_path,
        delete=False
    ) as f:
        f.write(rules)
        rules_file = Path(f.name)
    
    # Pre-flight: reject disjunction in rule bodies.
    # RTEC's compiler strips parentheses, turning (A ; B) into a clause-level
    # disjunction whose second branch has no head and no variable bindings from
    # happensAt — causing runtime instantiation errors. Catch it here so the
    # agent sees a clear error before the compiled file is written.
    semicolon_errors = _check_no_semicolon(rules)
    if semicolon_errors:
        rules_file.unlink(missing_ok=True)
        return CompileResult(success=False, errors=semicolon_errors)

    arithmetic_errors = _check_singleton_arithmetic(rules)
    if arithmetic_errors:
        rules_file.unlink(missing_ok=True)
        return CompileResult(success=False, errors=arithmetic_errors)

    try:
        # Run RTEC compiler
        result = subprocess.run(
            [
                "swipl", "-l", str(RTEC_COMPILER),
                "-g", f"compileED('{rules_file}', withoutOptimisation), halt.",
            ],
            capture_output=True,
            text=True,
            timeout=30
        )
        
        # Parse output for errors
        errors = []
        warnings = []
        
        for line in result.stderr.split('\n'):
            line = line.strip()
            if not line:
                continue
            if 'ERROR' in line or 'error' in line.lower():
                errors.append(line)
            elif 'Warning' in line or 'warning' in line.lower():
                warnings.append(line)
        
        # Also check stdout for Prolog errors
        for line in result.stdout.split('\n'):
            if 'ERROR' in line:
                errors.append(line)
        
        # Check return code
        if result.returncode != 0 and not errors:
            errors.append(f"Compilation failed with exit code {result.returncode}")
            if result.stderr:
                errors.append(result.stderr[:500])
        
        success = len(errors) == 0
        
        # If successful, copy to generated_rules.prolog
        if success:
            compiled_file = rules_file.with_name('compiled_rules.prolog')
            if compiled_file.exists():
                # Move compiled rules to app directory
                target = app_path / "generated_rules_compiled.prolog"
                compiled_file.rename(target)
            
            # Also save the source rules
            source_target = app_path / "generated_rules.prolog"
            rules_file.rename(source_target)
        else:
            # Clean up on failure
            rules_file.unlink(missing_ok=True)
        
        return CompileResult(
            success=success,
            errors=errors,
            warnings=warnings
        )
        
    except subprocess.TimeoutExpired:
        rules_file.unlink(missing_ok=True)
        return CompileResult(
            success=False,
            errors=["Compilation timed out after 30 seconds"]
        )
    except Exception as e:
        rules_file.unlink(missing_ok=True)
        return CompileResult(
            success=False,
            errors=[f"Compilation error: {str(e)}"]
        )
