"""`hermes neo4j-memory <command>`

  status        credentials + Bolt connectivity (fast, no model load)
  stats         node counts per label + embedding-model marker
  dream         duplicate report; --apply merges the safe (exact-name) pairs
  reembed       recompute entity vectors after an embedding-model change
  digest        build the always-on core digest for the system prompt (--write to save)
  query         read-only Cypher (no argument = schema + examples)
  install-cron  create (or --update) the nightly dreaming job in Hermes' cron scheduler
  merge / reject / search / tool   the same operations the agent tools offer, for cron agents
                                   (cron sessions get no provider tools, but they can use the terminal)
"""

import asyncio
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

_SIDECAR = {"NEO4J_URI": "uri", "NEO4J_USERNAME": "username", "NEO4J_DATABASE": "database"}


def _core():
    """Load the provider module that lives next to this file (same code Hermes runs)."""
    import importlib.util

    name = "neo4j_memory_core"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name("__init__.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _setting(name: str) -> str:
    try:
        from agent.secret_scope import get_secret
        value = get_secret(name)
    except Exception:
        value = None
    value = value or os.environ.get(name)
    if not value and name in _SIDECAR:
        try:
            from hermes_constants import get_hermes_home
            home = Path(get_hermes_home())
        except Exception:
            home = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
        try:
            value = json.loads((home / "neo4j-memory.json").read_text(encoding="utf-8")).get(_SIDECAR[name])
        except Exception:
            value = None
    return str(value or "").strip()


def _credentials():
    names = ("NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD", "NEO4J_DATABASE")
    for n in names:
        print(f"{n:16} {'set' if _setting(n) else 'MISSING'}")
    if not all(_setting(n) for n in names[:3]):
        print("Provider would report 'unavailable' (credentials missing in this environment).")
        return None
    return _setting("NEO4J_URI"), _setting("NEO4J_USERNAME"), _setting("NEO4J_PASSWORD"), _setting("NEO4J_DATABASE")


async def _bolt_query(creds, cypher: str):
    from neo4j import AsyncGraphDatabase

    uri, user, password, database = creds
    async with AsyncGraphDatabase.driver(uri, auth=(user, password)) as driver:
        await driver.verify_connectivity()
        kwargs = {"database_": database} if database else {}
        records, _, _ = await driver.execute_query(cypher, **kwargs)
        return [r.data() for r in records]


def _run(creds, cypher, render) -> None:
    try:
        render(asyncio.run(_bolt_query(creds, cypher)))
    except Exception as exc:
        print(f"Connection failed: {type(exc).__name__}: {exc}")


def _builtin_report() -> None:
    try:
        core = _core()
        home = core._hermes_home()
        st = core._builtin_memory_state(home)
    except Exception as exc:
        print(f"Built-in memory: could not be determined ({type(exc).__name__})")
        return
    mem_on, user_on = st["memory_enabled"], st["user_profile_enabled"]
    if st["standalone"]:
        mode = "OFF (this plugin is the only persistent memory)"
    elif user_on and not mem_on:
        mode = "PARTLY on: user profile (USER.md) on, MEMORY.md off (this plugin holds everything else)"
    else:
        mode = "on (this plugin complements it)"
    print(f"Built-in memory: {mode}" + ("" if st["known"] else "  [config.yaml not readable, assuming defaults]"))
    if st["toolset_disabled"]:
        print("WARNING: 'memory' is listed in agent.disabled_toolsets. Hermes then also HIDES the tools of external "
              "memory providers. Remove it there and set memory.memory_enabled: false and "
              "memory.user_profile_enabled: false instead.")
    digest = home / core.DIGEST_FILE
    if not mem_on:
        print("Core digest: " + (f"{digest.stat().st_size} bytes, updated "
                                 f"{__import__('datetime').datetime.fromtimestamp(digest.stat().st_mtime):%Y-%m-%d %H:%M}"
                                 if digest.exists() else "not created yet (run `hermes neo4j-memory digest --write`)"))


def _status(args) -> None:
    creds = _credentials()
    if creds:
        _run(creds, "MATCH (n) RETURN count(n) AS nodes",
             lambda rows: print(f"Connected. Nodes in graph: {rows[0]['nodes']}"))
    _builtin_report()


def _stats(args) -> None:
    creds = _credentials()
    if not creds:
        return

    def render(rows):
        print("Connected. Nodes by label:")
        for r in rows:
            print(f"  {r['label']:<22} {r['n']}")

    _run(creds, "MATCH (n) UNWIND labels(n) AS label RETURN label, count(*) AS n ORDER BY n DESC", render)

    def render_marker(rows):
        try:
            configured = _core()._embedding_model()
        except Exception:
            configured = "?"
        graph_model = rows[0]["v"] if rows else "(unmarked; pre-0.3 graphs used BAAI/bge-small-en-v1.5)"
        flag = "OK" if graph_model == configured else "MISMATCH -> run `hermes neo4j-memory reembed`"
        print(f"Embedding model: graph={graph_model} | configured={configured} | {flag}")

    _run(creds, "OPTIONAL MATCH (m:HermesMemoryMeta {key:'embedding_model'}) "
                "WITH m WHERE m IS NOT NULL RETURN m.value AS v", render_marker)

    def render_ft(rows):
        if rows:
            print(f"Full-text index {rows[0]['name']}: {rows[0]['state']}")
        else:
            print("Full-text index hermes_entity_fulltext: not created yet (it is created on the first connection "
                  "of plugin 0.4.0+; search falls back to vector-only until then)")

    _run(creds, "SHOW INDEXES YIELD name, state WHERE name = 'hermes_entity_fulltext' RETURN name, state", render_ft)


def _provider(args=None):
    core = _core()
    provider = core.Neo4jMemoryProvider()
    if not provider.is_available():
        raise SystemExit("neo4j-memory: credentials missing or neo4j-agent-memory not installed. "
                         "Run `hermes neo4j-memory status`.")
    kwargs = {"agent_context": "cron"}   # non-primary: no automatic writes, maintenance tool allowed
    user = getattr(args, "user", "") if args is not None else ""
    if user:
        kwargs["user_id"] = user          # NEO4J_MEMORY_USER (env) still wins, same as in live sessions
    provider.initialize("cli-maintenance", **kwargs)
    provider.cli_ready()
    return provider


def _dream(args) -> None:
    provider = _provider(args)
    try:
        print("Analysing graph (the first run may download the embedding model)...", file=sys.stderr)
        report = provider.cli_dream_report(semantic=not args.no_semantic)
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            s = report["stats"]
            print(f"Identity: {report.get('identity')}")
            if report.get("warning"):
                print("WARNING: " + report["warning"])
            print(f"Entities: {s['entities']} | relationships: {s['relationships']} | "
                  f"active preferences: {s['preferences_active']} | embedding ok: {s['embedding_ok']}")
            for p in report["duplicates"]:
                k, d = p["keep"], p["drop"]
                print(f"  [{p['rule']}] keep '{k['name']}' ({k['type']}, {k['degree']} links) "
                      f"<- drop '{d['name']}' ({d['degree']} links)")
            if not report["duplicates"]:
                print("  no duplicate candidates")
            if report["orphans"]:
                print("Entities without any links: " + ", ".join(report["orphans"]))
        if args.apply:
            safe = [p for p in report["duplicates"] if p["rule"] == "exact_norm"]
            for p in safe:
                res = provider.cli_merge(p["keep"]["id"], p["drop"]["id"])
                print(f"  merge '{p['drop']['name']}' -> '{p['keep']['name']}': {res.get('status')}",
                      file=sys.stderr if args.json else sys.stdout)
            print(f"Applied {len(safe)} safe merge(s). Other candidates are left for the nightly agent job.",
                  file=sys.stderr if args.json else sys.stdout)
        elif report["duplicates"] and not args.json:
            print("Dry run. `--apply` merges only [exact_norm] pairs; judgement calls belong to the nightly job.")
    finally:
        provider.shutdown()


def _reembed(args) -> None:
    provider = _provider(args)
    try:
        print("Re-embedding entities with the configured model. Pause other Hermes sessions first.")
        res = provider.cli_reembed()
        print(json.dumps(res, ensure_ascii=False, indent=2))
    finally:
        provider.shutdown()


def _emit(provider, name, payload) -> None:
    print(provider.cli_tool(name, payload))


def _merge(args) -> None:
    provider = _provider(args)
    try:
        _emit(provider, "neo4j_memory_maintain", {"action": "merge", "keep_id": args.keep_id, "drop_id": args.drop_id})
    finally:
        provider.shutdown()


def _reject(args) -> None:
    provider = _provider(args)
    try:
        _emit(provider, "neo4j_memory_maintain", {"action": "reject", "keep_id": args.id_a, "drop_id": args.id_b})
    finally:
        provider.shutdown()


def _search(args) -> None:
    provider = _provider(args)
    try:
        _emit(provider, "neo4j_memory_search", {"query": args.query, "limit": args.limit})
    finally:
        provider.shutdown()


_TOOLS = ("neo4j_memory_search", "neo4j_memory_remember", "neo4j_memory_update", "neo4j_memory_forget")


def _digest(args) -> None:
    provider = _provider(args)
    try:
        profiles = [x for x in (args.profiles or "").split(",") if x.strip()] or None
        res = provider.cli_digest(write=args.write, profiles=profiles)
        previews = [(t["profile"], t.pop("text", "")) for t in res["targets"]]
        print(json.dumps(res, ensure_ascii=False, indent=2))
        if not args.write:
            for name, text in previews:
                print(f"\n--- preview for profile '{name}' (use --write to save) ---\n{text}")
    finally:
        provider.shutdown()


def _query_cmd(args) -> None:
    provider = _provider(args)
    try:
        params = json.loads(args.params) if args.params else {}
        _emit(provider, "neo4j_memory_query", {"cypher": args.cypher, "params": params, "max_rows": args.max_rows})
    finally:
        provider.shutdown()


def _tool(args) -> None:
    if args.name not in _TOOLS:
        raise SystemExit(f"unknown tool '{args.name}'. Choose one of: {', '.join(_TOOLS)}")
    try:
        if args.args_file:
            raw = Path(args.args_file).read_text(encoding="utf-8-sig")
        elif args.args:
            raw = args.args
        else:
            raw = sys.stdin.read()
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("JSON must be an object")
    except Exception as exc:
        raise SystemExit(f"could not read the JSON arguments ({exc}). Use --args-file <file>.")
    provider = _provider(args)
    try:
        _emit(provider, args.name, payload)
    finally:
        provider.shutdown()


def _install_cron(args) -> None:
    every = int(args.every_hours or 0)
    if every:
        schedule = f"{int(args.minute)} */{every} * * *"
        prompt = _core().dream_prompt(every + 1)      # one hour of overlap; the agent searches before adding
    else:
        schedule = f"{int(args.minute)} {int(args.hour)} * * *"
        prompt = _core().dream_prompt(24)
    base = [shutil.which("hermes") or "hermes"]
    if args.profile:
        base += ["-p", args.profile]
    if args.update:   # replace the prompt (and schedule) of the existing job
        cmd = base + ["cron", "edit", "neo4j-memory-dream", "--prompt", prompt, "--schedule", schedule]
        shown = " ".join(base[1:]) + f' cron edit neo4j-memory-dream --prompt "<dream prompt>" --schedule "{schedule}"'
    else:
        cmd = base + ["cron", "create", schedule, prompt, "--name", "neo4j-memory-dream", "--deliver", "local"]
        shown = " ".join(base[1:]) + f' cron create "{schedule}" "<dream prompt>" --name neo4j-memory-dream --deliver local'
    shown = "hermes " + shown.strip()
    if not args.apply:
        print("Dry run. Command that would be executed:\n  " + shown)
        print("\nPrompt:\n" + prompt)
        print("\nRun again with --apply. Verify with `hermes cron list` and `hermes cron run neo4j-memory-dream`.")
        return
    try:
        subprocess.run(cmd, check=True)
    except Exception as exc:
        print(f"Could not {'update' if args.update else 'create'} the cron job: {exc}\nRun it manually:\n  {shown}")


def _dispatch(args) -> None:
    {
        "status": _status, "stats": _stats, "dream": _dream, "reembed": _reembed,
        "install-cron": _install_cron, "merge": _merge, "reject": _reject,
        "search": _search, "tool": _tool, "query": _query_cmd, "digest": _digest,
    }.get(getattr(args, "neo4j_memory_cmd", None) or "status", _status)(args)


def register_cli(subparser) -> None:
    subs = subparser.add_subparsers(dest="neo4j_memory_cmd")

    def with_user(p):
        p.add_argument("--user", default="", help="user identifier (default: NEO4J_MEMORY_USER or 'default'); "
                                                    "must match the live sessions' identity")
        return p

    subs.add_parser("status", help="Check credentials and Bolt connectivity (default)")
    subs.add_parser("stats", help="Node counts per label and embedding-model marker")
    dream = with_user(subs.add_parser("dream", help="Duplicate report; --apply merges safe exact-name pairs"))
    dream.add_argument("--apply", action="store_true")
    dream.add_argument("--json", action="store_true")
    dream.add_argument("--no-semantic", action="store_true", help="skip embedding-based candidates (faster)")
    with_user(subs.add_parser("reembed", help="Recompute entity vectors after an embedding-model change"))
    merge = with_user(subs.add_parser("merge", help="Merge drop_id into keep_id (same type; ids from `dream --json`)"))
    merge.add_argument("keep_id")
    merge.add_argument("drop_id")
    reject = with_user(subs.add_parser("reject", help="Record that two entities are different (not proposed again)"))
    reject.add_argument("id_a")
    reject.add_argument("id_b")
    search = with_user(subs.add_parser("search", help="Search entities and preferences"))
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=5)
    digest = with_user(subs.add_parser("digest", help="Build the core digest for the system prompt"))
    digest.add_argument("--write", action="store_true", help="save it (default: preview only)")
    digest.add_argument("--profiles", default="", help="comma list of profiles (default: NEO4J_MEMORY_DIGEST_PROFILES or this one)")
    query = with_user(subs.add_parser("query", help="Read-only Cypher; without an argument: schema and examples"))
    query.add_argument("cypher", nargs="?", default="")
    query.add_argument("--params", default="", help='JSON object, e.g. \'{"name": "Maya"}\'')
    query.add_argument("--max-rows", type=int, default=50)
    tool = with_user(subs.add_parser("tool", help="Run remember/update/forget/search with JSON arguments"))
    tool.add_argument("name", help=" | ".join(_TOOLS))
    tool.add_argument("--args-file", default="", help="JSON file (recommended; inline JSON breaks in Windows shells)")
    tool.add_argument("--args", default="", help="inline JSON; if neither is given, JSON is read from stdin")
    cron = subs.add_parser("install-cron", help="Create or update the nightly dreaming job in Hermes cron")
    cron.add_argument("--hour", default="3")
    cron.add_argument("--minute", default="30")
    cron.add_argument("--every-hours", type=int, default=0, help="run every N hours instead of daily (e.g. 6)")
    cron.add_argument("--profile", default="", help="Hermes profile to create the job in")
    cron.add_argument("--update", action="store_true", help="replace prompt/schedule of the existing job")
    cron.add_argument("--apply", action="store_true", help="actually run the command (default: dry run)")
    subparser.set_defaults(func=_dispatch)
