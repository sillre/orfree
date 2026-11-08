"""Command line interface for orfree."""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any

from . import ccswitch, ccswitch_writer, daily, keyring, openrouter, snapshot

APP = "codex"


def _api_key(explicit: str | None = None) -> str:
    """The user's own key: explicit argument, env var, or the local store.

    Deliberately never reads another application's configuration: a public
    build must not silently use a key that belongs to somebody else.
    """
    if explicit:
        return explicit
    env = os.environ.get("OPENROUTER_API_KEY")
    if env:
        return env
    saved = keyring.load_key()
    if saved:
        return saved
    raise SystemExit(
        "No OpenRouter API key found. Create one at https://openrouter.ai/keys, "
        "then either:\n"
        "  - open the desktop window and click 'API Key', or\n"
        "  - run: python -m orfree set-key\n"
        "  - or set the OPENROUTER_API_KEY environment variable."
    )


def _fmt_price(value: float) -> str:
    """A price per million tokens, or "free"."""
    if value == 0:
        return "free"
    return "$%.2f/M" % value


def _fmt_size(value: int) -> str:
    """A context window in human units."""
    if value >= 1_000_000:
        return "%.1fM" % (value / 1_000_000)
    if value >= 1000:
        return "%dK" % (value // 1000)
    return str(value)


def _table(rows: list[list[str]], headers: list[str]) -> str:
    """An aligned text table."""
    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))
    lines = ["  ".join(header.ljust(widths[i]) for i, header in enumerate(headers))]
    lines.append("  ".join("-" * widths[i] for i in range(len(headers))))
    for row in rows:
        lines.append("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)))
    return "\n".join(lines)


def cmd_list(args: argparse.Namespace) -> int:
    """Show the free models OpenRouter offers."""
    key = _api_key(args.api_key)
    models = openrouter.fetch_models(api_key=key, with_endpoints=not args.fast)
    free = [model for model in models if model.free]

    if args.snapshot:
        snapshot.save_snapshot(
            snapshot.today(),
            {model.id: snapshot.model_entry(model) for model in models},
        )

    fresh = snapshot.just_added(models)
    info = openrouter.key_info(key)
    print(
        "OpenRouter: %d models total, %d free.  Key cap %s, remaining %s."
        % (len(models), len(free), info.get("limit"), info.get("limit_remaining"))
    )
    if fresh:
        shown = ", ".join(sorted(fresh)[:5])
        more = "..." if len(fresh) > 5 else ""
        print("New since yesterday: %d (%s%s)" % (len(fresh), shown, more))
    print("")

    rows = []
    for model in sorted(free, key=lambda item: item.id):
        providers = ", ".join(model.provider_names[:2]) or "-"
        if len(model.provider_names) > 2:
            providers += "+%d" % (len(model.provider_names) - 2)
        rows.append(
            [
                "NEW" if model.id in fresh else "",
                model.id,
                _fmt_size(model.context_length),
                _fmt_price(model.prompt_price),
                _fmt_price(model.completion_price),
                "yes" if model.supports_reasoning else "",
                "yes" if model.supports_tools else "",
                providers,
            ]
        )
    headers = ["", "MODEL", "CTX", "IN", "OUT", "RSN", "TOOLS", "PROVIDERS"]
    print(_table(rows, headers))
    return 0


def cmd_probe(args: argparse.Namespace) -> int:
    """Ask OpenRouter whether models actually answer right now."""
    key = _api_key(args.api_key)
    models = openrouter.fetch_models(api_key=key, with_endpoints=False)
    targets = [model for model in models if model.free]
    if args.models:
        wanted = set(args.models)
        targets = [model for model in targets if model.id in wanted]
    if not targets:
        print("No free models matched.")
        return 1

    print("Probing %d free models..." % len(targets))
    results = []
    for model in targets:
        result = openrouter.probe_model(model.id, key)
        results.append(result)
        print(
            "  %-4s %-48s %s"
            % ("OK" if result.ok else "FAIL", model.id, result.reason)
        )
    working = [result.model_id for result in results if result.ok]
    print("")
    print("%d/%d free models answered." % (len(working), len(results)))
    if working:
        print("Working: %s" % ", ".join(working))
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    """Create a new CC Switch provider and optionally route Codex to it."""
    key = _api_key(args.api_key)
    models = openrouter.fetch_models(api_key=key, with_endpoints=False)
    catalogue = {model.id: model for model in models}

    selected = list(args.models or [])
    if args.free_only and not selected:
        selected = [model.id for model in models if model.free]
    if not selected:
        print("Nothing selected. Pass --model ID (repeatable) or --free-only.")
        return 1

    chosen = []
    for model_id in selected:
        model = catalogue.get(model_id)
        if not model:
            print("Unknown model: %s" % model_id)
            return 1
        chosen.append(model)

    if args.probe:
        print("Probing the selection before importing...")
        reachable = []
        for model in chosen:
            result = openrouter.probe_model(model.id, key)
            print(
                "  %-4s %s %s"
                % ("OK" if result.ok else "FAIL", model.id, result.reason)
            )
            if result.ok:
                reachable.append(model)
        if not reachable:
            print("No selected model answered; nothing imported.")
            return 1
        chosen = reachable

    name = args.name or ("OpenRouter Free " + snapshot.today())
    current = ccswitch.routing_for(APP)

    result = ccswitch_writer.create_provider(
        name=name,
        models=chosen,
        api_key=key,
        notes=args.notes,
        app=APP,
        include_current=not args.no_keep_current,
        default_model=args.default_model,
    )
    provider_id = result.provider_id
    print("")
    print("Backup: %s" % result.backup_folder)
    print("Created provider %s (%s) with %d models." % (name, provider_id, len(chosen)))

    state = daily.load_state()
    state["managed_provider_id"] = provider_id
    daily.save_state(state)
    print("This card is now the one 'orfree daily' keeps up to date.")

    if args.route:
        ccswitch_writer.route_to(provider_id, APP)
        print("Routed codex to %s (was %s)." % (provider_id, current.proxy_route or "none"))
    else:
        print(
            "Not routed (no --route). The current route %s is untouched."
            % (current.proxy_route or "none")
        )
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """Show what CC Switch is routing right now."""
    state = ccswitch.routing_for(APP)
    print("CC Switch proxy: %s" % ("running" if ccswitch.proxy_health() else "not running"))
    print("codex mode: %s  attached: %s" % (state.mode, state.attached))
    print("codex route: %s" % (state.proxy_route or "none"))
    print("")
    rows = []
    for provider in ccswitch.list_providers(APP):
        specs = provider["settings_config"].get("modelCatalog", {}).get("models", [])
        marker = "*" if provider["id"] == state.proxy_route else " "
        names = ", ".join(spec.get("model", "") for spec in specs[:2])[:58]
        rows.append(
            [marker, provider["name"], provider["id"][:8], str(len(specs)), names]
        )
    print(_table(rows, ["", "NAME", "ID", "N", "MODELS"]))
    return 0


def cmd_snapshot(args: argparse.Namespace) -> int:
    """Refresh the stored catalogue snapshot."""
    key = _api_key(args.api_key)
    models = openrouter.fetch_models(api_key=key, with_endpoints=False)
    path = snapshot.save_snapshot(
        snapshot.today(), {model.id: snapshot.model_entry(model) for model in models}
    )
    fresh = snapshot.just_added(models)
    gone = snapshot.gone_since(models)
    print("Saved %d models to %s" % (len(models), path))
    if fresh:
        print("New: %s" % ", ".join(sorted(fresh)))
    if gone:
        print("Gone: %s" % ", ".join(sorted(gone)))
    return 0


def cmd_set_key(args: argparse.Namespace) -> int:
    """Interactively store the user's own key on this machine."""
    import getpass

    key = getpass.getpass("OpenRouter API key (input hidden): ").strip()
    if not key:
        print("Nothing saved.")
        return 1
    keyring.save_key(key)
    print("Saved to %s" % keyring.config_path())
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    """Restore CC Switch's files from a backup."""
    folder = args.folder or ccswitch.latest_backup()
    if not folder:
        print("No backup found.")
        return 1
    restored = ccswitch.restore_backup(folder)
    print("Restored from %s:" % folder)
    for path in restored:
        print("  %s" % path)
    print("")
    print("Restart CC Switch to pick the restored state up.")
    return 0


def cmd_daily(args: argparse.Namespace) -> int:
    """Refresh the catalogue and update the card this tool manages."""
    key = _api_key(args.api_key)
    print("Refreshing the OpenRouter catalogue...")
    return daily.refresh(key, probe=not args.no_probe, dry_run=args.dry_run)


def build_parser() -> argparse.ArgumentParser:
    """Command line parser."""
    parser = argparse.ArgumentParser(
        prog="orfree",
        description="Pick OpenRouter models and wire them into CC Switch.",
    )
    parser.add_argument(
        "--api-key",
        help="your OpenRouter key (default: ~/.orfree/config.json or OPENROUTER_API_KEY)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    listing = sub.add_parser("list", help="list the free models OpenRouter offers")
    listing.add_argument("--fast", action="store_true", help="skip endpoint details")
    listing.add_argument("--snapshot", action="store_true", help="also store a daily snapshot")
    listing.set_defaults(func=cmd_list)

    probe = sub.add_parser("probe", help="check which free models answer right now")
    probe.add_argument(
        "--model", dest="models", action="append", help="model id (repeatable)"
    )
    probe.set_defaults(func=cmd_probe)

    importer = sub.add_parser("import", help="create a CC Switch provider")
    importer.add_argument(
        "--model", dest="models", action="append", help="model id (repeatable)"
    )
    importer.add_argument("--free-only", action="store_true", help="import every free model")
    importer.add_argument("--name", help="card name in CC Switch")
    importer.add_argument("--notes", help="notes shown on the card")
    importer.add_argument("--route", action="store_true", help="route codex to the new card")
    importer.add_argument(
        "--default-model", help="model Codex should use after the switch"
    )
    importer.add_argument(
        "--no-keep-current",
        action="store_true",
        help="replace the current card's model list instead of adding to it",
    )
    importer.add_argument(
        "--probe", action="store_true", help="only import models that answer"
    )
    importer.set_defaults(func=cmd_import)

    status = sub.add_parser("status", help="show the current CC Switch routing")
    status.set_defaults(func=cmd_status)

    snap = sub.add_parser("snapshot", help="refresh the stored catalogue snapshot")
    snap.set_defaults(func=cmd_snapshot)

    restore = sub.add_parser("restore", help="restore CC Switch's files from a backup")
    restore.add_argument("--folder", help="backup folder (default: the newest one)")
    restore.set_defaults(func=cmd_restore)

    keycmd = sub.add_parser(
        "set-key", help="type in your own OpenRouter API key and store it locally"
    )
    keycmd.set_defaults(func=cmd_set_key)

    refresh = sub.add_parser(
        "daily", help="refresh the catalogue and update the managed card"
    )
    refresh.add_argument("--no-probe", action="store_true", help="skip the health check")
    refresh.add_argument(
        "--dry-run", action="store_true", help="report changes without writing"
    )
    refresh.set_defaults(func=cmd_daily)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    # Windows consoles default to a legacy code page; card names can hold
    # non-ASCII text, so print them as UTF-8.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except ccswitch.CcSwitchError as error:
        print("Error: %s" % error, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
