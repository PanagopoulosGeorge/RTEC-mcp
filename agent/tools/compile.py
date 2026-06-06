"""Compile RTEC rules."""

import subprocess
import tempfile
from pathlib import Path

from ..config import RTEC_COMPILER, APPS_DIR
from ..core.schemas import CompileResult


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
