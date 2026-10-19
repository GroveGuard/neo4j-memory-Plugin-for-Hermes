"""Run the stub-based test suites:  python tests/run_all.py

Each suite runs in a fresh temporary directory with stubs for Hermes ("agent"), the Neo4j driver ("neo4j")
and "neo4j_agent_memory". They verify the plugin's own logic; they do NOT test the real SDK or a database.
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SUITES = ["core", "cli_commands", "features", "standalone", "recall", "catalog"]


def run_suite(name: str) -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        for pkg in ("agent", "neo4j", "neo4j_agent_memory", "sentence_transformers"):
            shutil.copytree(HERE / "stubs" / pkg, work / pkg)
        (work / "plug").mkdir()
        (work / "plug_dir").mkdir()
        shutil.copy(ROOT / "__init__.py", work / "plug" / "neo4j_memory_plugin.py")
        shutil.copy(ROOT / "cli.py", work / "plug" / "cli_plugin.py")
        shutil.copy(ROOT / "__init__.py", work / "plug_dir" / "__init__.py")
        shutil.copy(ROOT / "cli.py", work / "plug_dir" / "cli.py")
        shutil.copy(ROOT / "plugin.yaml", work / "plugin.yaml")
        shutil.copy(HERE / "lint_catalog.py", work / "lint_catalog.py")
        shutil.copytree(HERE / "fixtures", work / "fixtures")
        shutil.copy(HERE / "suites" / f"{name}.py", work / "suite.py")
        proc = subprocess.run([sys.executable, "suite.py"], cwd=work, capture_output=True, text=True)
        lines = [l for l in proc.stdout.splitlines() if l.strip()]
        verdict = next((l for l in reversed(lines) if l.startswith(("ALL ", "FAIL "))), "no result")
        ok = proc.returncode == 0 and verdict.startswith("ALL ")
        print(f"{'ok  ' if ok else 'FAIL'} {name}: {verdict}")
        if not ok:
            print("\n".join(lines[-12:]))
            print(proc.stderr[-1500:])
        return ok


def run_lint() -> bool:
    proc = subprocess.run([sys.executable, str(HERE / "lint_catalog.py")], cwd=ROOT, capture_output=True, text=True)
    print(f"{'ok  ' if proc.returncode == 0 else 'FAIL'} catalog lint: {proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else proc.stderr[-200:]}")
    return proc.returncode == 0


if __name__ == "__main__":
    results = [run_suite(n) for n in SUITES] + [run_lint()]
    sys.exit(0 if all(results) else 1)
