"""Execute RTEC recognition."""

import subprocess
import re
from pathlib import Path

from ..config import RTEC_SCRIPTS, APPS_DIR, REPO_ROOT, RTEC_COMPILER
from ..core.schemas import Recognition


def _compile_rules_file(rules_file: Path, target_compiled: Path) -> None:
    """Compile a rules file and move compiled output to target path."""
    result = subprocess.run(
        [
            "swipl",
            "-l",
            str(RTEC_COMPILER),
            "-g",
            f"compileED('{rules_file}', withoutOptimisation), halt.",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=REPO_ROOT,
    )

    if result.returncode != 0:
        error_text = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"RTEC compilation failed: {error_text}")

    compiled_file = rules_file.with_name("compiled_rules.prolog")
    if not compiled_file.exists():
        raise RuntimeError("RTEC compilation did not produce compiled_rules.prolog")

    compiled_file.replace(target_compiled)


def parse_recognitions(output_file: Path) -> list[Recognition]:
    """Parse RTEC output file into Recognition objects."""
    recognitions = []
    
    if not output_file.exists():
        return recognitions
    
    # Pattern: recognitions(predictions,fluent,[[args],value],[(start,end),...]).
    pattern = re.compile(
        r"recognitions\(predictions,(\w+),\[\[([^\]]*)\],([^\]]+)\],\[([^\]]+)\]\)"
    )
    
    with open(output_file, 'r') as f:
        for line in f:
            match = pattern.search(line)
            if match:
                fluent = match.group(1)
                args_str = match.group(2)
                value = match.group(3).strip()
                intervals_str = match.group(4)
                
                # Parse args
                args = [a.strip() for a in args_str.split(',') if a.strip()]
                
                # Parse intervals
                intervals = []
                interval_pattern = re.compile(r'\((\d+),(\d+)\)')
                for int_match in interval_pattern.finditer(intervals_str):
                    start = int(int_match.group(1))
                    end = int(int_match.group(2))
                    intervals.append((start, end))
                
                recognitions.append(Recognition(
                    fluent=fluent,
                    args=args,
                    value=value,
                    intervals=intervals
                ))
    
    return recognitions


def run_rtec(app: str, use_generated: bool = True) -> list[Recognition]:
    """
    Run RTEC event recognition.
    
    Args:
        app: Application name
        use_generated: If True, use generated_rules.prolog; else use expert_rules.prolog
        
    Returns:
        List of Recognition objects
    """
    app_path = APPS_DIR / app
    if not app_path.exists():
        raise ValueError(f"Application '{app}' not found in {APPS_DIR}")
    
    # Load app config
    config_file = app_path / "config.yaml"
    if config_file.exists():
        import yaml
        with open(config_file) as f:
            config = yaml.safe_load(f)
    else:
        # Default config
        config = {
            "window_size": 10,
            "step": 10, 
            "start_time": 0,
            "end_time": 100,
        }
    
    # Determine which rules to use
    if use_generated:
        compiled_target = app_path / "generated_rules_compiled.prolog"
        rules_file = compiled_target
        if not rules_file.exists():
            source_rules = app_path / "generated_rules.prolog"
            if source_rules.exists():
                _compile_rules_file(source_rules, compiled_target)
                rules_file = compiled_target
            else:
                rules_file = source_rules
    else:
        compiled_target = app_path / "expert_rules_compiled.prolog"
        rules_file = compiled_target
        if not rules_file.exists():
            source_rules = app_path / "expert_rules.prolog"
            if source_rules.exists():
                _compile_rules_file(source_rules, compiled_target)
                rules_file = compiled_target
            else:
                rules_file = source_rules
    
    if not rules_file.exists():
        raise ValueError(f"Rules file not found: {rules_file}")
    
    # Find input stream
    input_file = app_path / "input_stream.csv"
    if not input_file.exists():
        raise ValueError(f"Input stream not found: {input_file}")
    
    # Results directory
    results_dir = app_path / "results"
    results_dir.mkdir(exist_ok=True)
    
    # Build auxiliary files list
    aux_dir = app_path / "auxiliary"
    aux_files = []
    if aux_dir.exists():
        aux_files = list(aux_dir.glob("*.prolog"))
    
    # Build event description files list
    event_desc_files = [str(rules_file)] + [str(f) for f in aux_files]
    
    # Run RTEC via continuousQueries.prolog
    continuous_queries = RTEC_SCRIPTS / "continuousQueries.prolog"
    
    param_string = (
        f"window_size={config['window_size']}, "
        f"step={config['step']}, "
        f"start_time={config['start_time']}, "
        f"end_time={config['end_time']}, "
        f"event_description_files={event_desc_files}, "
        f"input_mode=csv, "
        f"input_providers=['{input_file}'], "
        f"results_directory='{results_dir}'"
    )
    
    prolog_goal = f"continuousQueries({app}, [{param_string}]), halt."
    
    result = subprocess.run(
        ["swipl", "-l", str(continuous_queries), "-g", prolog_goal],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=REPO_ROOT
    )
    
    if result.returncode != 0:
        raise RuntimeError(f"RTEC execution failed: {result.stderr}")
    
    # Find and parse output file
    output_files = list(results_dir.glob("*recognised-intervals.txt"))
    if not output_files:
        return []
    
    # Use most recent
    output_file = max(output_files, key=lambda p: p.stat().st_mtime)
    return parse_recognitions(output_file)
