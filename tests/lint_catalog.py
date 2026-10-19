"""Static lint for the Hermes plugin-catalog rules that can be checked without Hermes.

    python tests/lint_catalog.py

Mirrors what a reviewer / `hermes plugins validate` is documented to look at (catalog rules 3, 7, 9, 11, 12, 17).
It is NOT the official validator: run `hermes plugins validate <path> --install-deps` for that.
Errors fail; warnings list constructs a scanner may report as "caution" and that must be disclosed in the README.
"""
import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ["__init__.py", "cli.py"]
FORBIDDEN_CALLS = {"eval", "exec", "compile", "__import__", "setattr", "delattr", "globals", "locals"}
FORBIDDEN_MODULES = {"base64", "marshal", "pickle", "ctypes", "socket", "requests", "urllib", "http", "ftplib", "telnetlib", "pty"}
FORBIDDEN_OS = {"system", "popen", "execv", "execve", "spawnl", "spawnv", "putenv", "unsetenv"}
BINARY_HEADERS = (b"\x7fELF", b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf", b"\x00asm")


def lint(root=ROOT):
    root = Path(root)
    errors, warnings = [], []
    for name in SOURCES:
        path = root / name
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            where = f"{name}:{getattr(node, 'lineno', '?')}"
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mods = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
                for m in mods:
                    if m.split(".")[0] in FORBIDDEN_MODULES:
                        errors.append(f"{where}: import of {m} (network/obfuscation/native code is not allowed or must be justified)")
            if isinstance(node, ast.Call):
                fn = node.func
                if isinstance(fn, ast.Name) and fn.id in FORBIDDEN_CALLS:
                    errors.append(f"{where}: call to {fn.id}() (rule 9/7)")
                if isinstance(fn, ast.Attribute):
                    if isinstance(fn.value, ast.Name) and fn.value.id == "os" and fn.attr in FORBIDDEN_OS:
                        errors.append(f"{where}: os.{fn.attr}() (rule 7/12)")
                    if isinstance(fn.value, ast.Name) and fn.value.id == "subprocess":
                        if name != "cli.py":
                            errors.append(f"{where}: subprocess outside cli.py")
                        if any(k.arg == "shell" and isinstance(k.value, ast.Constant) and k.value.value for k in node.keywords):
                            errors.append(f"{where}: subprocess with shell=True")
                        if node.args and not isinstance(node.args[0], (ast.List, ast.Name)):
                            errors.append(f"{where}: subprocess command is not a list")
                        warnings.append(f"{where}: subprocess.{fn.attr}() runs `hermes cron ...` on an explicit --apply (disclose, rule 13)")
                    if fn.attr == "exec_module":
                        warnings.append(f"{where}: exec_module() loads the sibling file when cli.py runs outside a package (fallback only)")
                    if isinstance(fn.value, ast.Attribute) and isinstance(fn.value.value, ast.Name) and fn.value.value.id == "os" \
                            and fn.value.attr == "environ" and fn.attr in ("update", "setdefault", "pop", "clear"):
                        errors.append(f"{where}: writes to os.environ (rule 9/11)")
            if isinstance(node, (ast.Assign, ast.AugAssign, ast.Delete)):
                targets = node.targets if isinstance(node, (ast.Assign, ast.Delete)) else [node.target]
                for t in targets:
                    if isinstance(t, ast.Subscript) and isinstance(t.value, ast.Attribute):
                        v = t.value
                        if isinstance(v.value, ast.Name) and v.value.id == "sys" and v.attr == "modules":
                            errors.append(f"{where}: write to sys.modules[...] (rule 9 'no core override')")
                        if isinstance(v.value, ast.Name) and v.value.id == "os" and v.attr == "environ":
                            errors.append(f"{where}: write to os.environ[...] (rule 9/11)")
                    if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id not in ("self", "mod", "result", "cls", "st", "t", "r", "tmp"):
                        if t.value.id in ("agent", "hermes_constants", "sys", "os", "importlib", "asyncio", "json"):
                            errors.append(f"{where}: attribute assignment on module/object '{t.value.id}' (rule 9)")
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                v = node.value
                if v in (".env", "auth.json", "credentials.json") or v.endswith(("/.env", "\\.env", "/auth.json")):
                    errors.append(f"{where}: reference to credential file {v!r} (rule 11)")
    # rule 17: binaries / bytecode / archives in the tracked tree
    try:
        files = subprocess.check_output(["git", "ls-files"], cwd=root, text=True, stderr=subprocess.DEVNULL).split()
    except Exception:
        files = [str(p.relative_to(root)) for p in root.rglob("*") if p.is_file() and ".git" not in p.parts]
    for f in files:
        if f.endswith((".pyc", ".pyo", ".so", ".dll", ".exe", ".dylib", ".wasm", ".zip", ".tar", ".gz", ".whl", ".jar")):
            errors.append(f"{f}: binary / bytecode / archive file (rule 17)")
            continue
        head = (root / f).read_bytes()[:4]
        if head.startswith(BINARY_HEADERS) or head[:2] == b"MZ":
            errors.append(f"{f}: binary header (rule 17)")
    return errors, warnings


if __name__ == "__main__":
    errors, warnings = lint()
    for w in warnings:
        print("warn ", w)
    for e in errors:
        print("ERROR", e)
    print("catalog lint:", "FAILED" if errors else "ok", f"({len(errors)} errors, {len(warnings)} warnings)")
    sys.exit(1 if errors else 0)
