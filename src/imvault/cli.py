"""Click CLI for imvault: list, export, view commands."""

import json
import logging
import os
import sys

import click

from . import __version__

DEFAULT_DB = os.path.expanduser("~/Library/Messages/chat.db")


def _setup_logging(verbosity: int) -> None:
    level = logging.WARNING
    if verbosity == 1:
        level = logging.INFO
    elif verbosity >= 2:
        level = logging.DEBUG
    logging.basicConfig(
        level=level,
        format="%(levelname)s: %(message)s",
    )


@click.group()
@click.version_option(__version__, prog_name="imvault")
@click.option(
    "--db-path",
    default=DEFAULT_DB,
    envvar="IMVAULT_DB_PATH",
    help="Path to chat.db (default: ~/Library/Messages/chat.db)",
    type=click.Path(readable=False),
)
@click.option("-v", "--verbose", count=True, help="Increase verbosity (-v info, -vv debug)")
@click.pass_context
def cli(ctx: click.Context, db_path: str, verbose: int) -> None:
    """imvault — Browse, search, and archive iMessage conversations."""
    ctx.ensure_object(dict)
    ctx.obj["db_path"] = db_path
    ctx.obj["verbose"] = verbose
    _setup_logging(verbose)


def _make_resolver():
    """Create a ContactResolver, returning None on failure."""
    try:
        from .contacts import ContactResolver
        return ContactResolver()
    except Exception:
        logging.getLogger(__name__).debug("Contact resolution unavailable", exc_info=True)
        return None


def _password_source(fd: int | None):
    """Return a callable that produces the next password.

    If fd is None, prompts interactively via click. Otherwise reads one
    password per line from the given file descriptor — the caller pipes
    passwords in via os.pipe() so they never appear on argv.
    """
    if fd is None:
        def _interactive(prompt_text: str, confirm: bool = False) -> str:
            return click.prompt(
                prompt_text,
                hide_input=True,
                confirmation_prompt=confirm,
            )
        return _interactive

    try:
        dup_fd = os.dup(fd)
    except OSError as e:
        raise click.ClickException(f"--password-fd {fd}: {e}") from e
    stream = os.fdopen(dup_fd, "r", encoding="utf-8")

    def _from_fd(prompt_text: str, confirm: bool = False) -> str:
        line = stream.readline()
        if not line:
            raise click.ClickException(
                "--password-fd: no password available (stream closed/empty)"
            )
        return line.rstrip("\r\n")

    return _from_fd


def _emit_event(event: str, *, chat_id: int | None, processed: int, total: int) -> None:
    """Write a single JSON event line to stderr for --progress-json consumers."""
    payload = {
        "event": event,
        "chat_id": chat_id,
        "processed": processed,
        "total": total,
    }
    sys.stderr.write(json.dumps(payload) + "\n")
    sys.stderr.flush()


@cli.command("list")
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit a JSON array of chats on stdout (for GUI/scripting consumers).",
)
@click.pass_context
def list_chats(ctx: click.Context, as_json: bool) -> None:
    """List all iMessage conversations."""
    from .db import IMMessageDB

    db_path = ctx.obj["db_path"]
    resolver = _make_resolver()
    try:
        db = IMMessageDB(db_path, resolver=resolver)
    except (FileNotFoundError, PermissionError) as e:
        if as_json:
            click.echo(json.dumps({"error": str(e)}), err=True)
        else:
            click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    with db:
        chats = db.list_chats()

    if as_json:
        payload = [
            {
                "chat_id": c["chat_id"],
                "display_name": c["display_name"],
                "participant_count": len(c["participants"]),
                "message_count": c["message_count"],
                "last_message_at": c["last_date"],
            }
            for c in chats
        ]
        click.echo(json.dumps(payload))
        return

    if not chats:
        click.echo("No conversations found.")
        return

    header = f"{'ID':>6}  {'Name':<40}  {'Messages':>8}  {'Last Date':<12}  Participants"
    separator = "-" * 100
    lines = [header, separator]
    for c in chats:
        name = c["display_name"][:40]
        last = (c["last_date"] or "")[:10]
        parts = ", ".join(c["participants"][:3])
        if len(c["participants"]) > 3:
            parts += f" +{len(c['participants']) - 3}"
        lines.append(
            f"{c['chat_id']:>6}  {name:<40}  {c['message_count']:>8}  {last:<12}  {parts}"
        )
    lines.append(f"\n{len(chats)} conversation(s) total.")

    click.echo_via_pager("\n".join(lines) + "\n")


@cli.command("export")
@click.option("--all", "export_all", is_flag=True, help="Export all conversations")
@click.option("--chat", "chat_ids", multiple=True, type=int, help="Chat ID(s) to export")
@click.option(
    "-o",
    "--output",
    "output_path",
    default=None,
    help="Output file path (default: imvault_export.imv)",
    type=click.Path(),
)
@click.option(
    "--password-fd",
    "password_fd",
    type=int,
    default=None,
    help="Read archive password from this file descriptor (no confirmation prompt).",
)
@click.option(
    "--progress-json",
    "progress_json",
    is_flag=True,
    help="Emit one JSON event per line on stderr while exporting.",
)
@click.pass_context
def export_chats(
    ctx: click.Context,
    export_all: bool,
    chat_ids: tuple[int, ...],
    output_path: str | None,
    password_fd: int | None,
    progress_json: bool,
) -> None:
    """Export iMessage conversations to an encrypted .imv archive."""
    from .archive import ArchiveBuilder
    from .db import IMMessageDB

    db_path = ctx.obj["db_path"]
    resolver = _make_resolver()
    try:
        db = IMMessageDB(db_path, resolver=resolver)
    except (FileNotFoundError, PermissionError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    get_password = _password_source(password_fd)

    with db:
        chats = db.list_chats()
        if not chats:
            click.echo("No conversations found.")
            return

        if export_all:
            selected_ids = [c["chat_id"] for c in chats]
        elif chat_ids:
            valid_ids = {c["chat_id"] for c in chats}
            selected_ids = []
            for cid in chat_ids:
                if cid not in valid_ids:
                    click.echo(f"Warning: chat ID {cid} not found, skipping.", err=True)
                else:
                    selected_ids.append(cid)
            if not selected_ids:
                click.echo("Error: No valid chat IDs provided.", err=True)
                sys.exit(1)
        else:
            # Interactive selector
            from .selector import select_chats

            selected_ids = select_chats(chats)
            if not selected_ids:
                click.echo("No conversations selected.")
                return

        if output_path is None:
            output_path = "imvault_export.imv"

        password = get_password(
            "Enter archive password", confirm=password_fd is None
        )

        if not password:
            click.echo("Error: Password cannot be empty.", err=True)
            sys.exit(1)

        if progress_json:
            click.echo(f"Exporting {len(selected_ids)} conversation(s)...")
            builder = ArchiveBuilder(
                db,
                password,
                output_path,
                selected_ids,
                event_callback=_emit_event,
            )
            result_path = builder.build()
        else:
            click.echo(f"Exporting {len(selected_ids)} conversation(s)...")
            with click.progressbar(
                length=len(selected_ids), label="Exporting conversations"
            ) as bar:
                builder = ArchiveBuilder(
                    db,
                    password,
                    output_path,
                    selected_ids,
                    progress=lambda cur, tot: bar.update(1),
                )
                result_path = builder.build()
        click.echo("Encrypting archive...")

    size = os.path.getsize(result_path)
    click.echo(f"Archive saved to {result_path} ({_format_size(size)})")


@cli.command("merge")
@click.argument("archives", nargs=-1, type=click.Path(exists=True))
@click.option(
    "-o",
    "--output",
    "output_path",
    default=None,
    help="Output file path (default: imvault_merged.imv)",
    type=click.Path(),
)
@click.option(
    "--separate-passwords",
    is_flag=True,
    help="Prompt for a separate password for each input archive",
)
@click.option(
    "--with-current",
    is_flag=True,
    help="Include messages from the current chat.db in the merged archive",
)
@click.option("--all", "include_all", is_flag=True, help="Include all current conversations")
@click.option("--chat", "chat_ids", multiple=True, type=int, help="Current chat ID(s) to include")
@click.option(
    "--password-fd",
    "password_fd",
    type=int,
    default=None,
    help=(
        "Read passwords from this file descriptor, one per line. Order: each "
        "input archive password (one if shared, one per archive if "
        "--separate-passwords), then the output archive password."
    ),
)
@click.pass_context
def merge_archives(
    ctx: click.Context,
    archives: tuple[str, ...],
    output_path: str | None,
    separate_passwords: bool,
    with_current: bool,
    include_all: bool,
    chat_ids: tuple[int, ...],
    password_fd: int | None,
) -> None:
    """Merge encrypted .imv archives into one deduplicated archive."""
    from .archive import MergedArchiveBuilder
    from .db import IMMessageDB

    if len(archives) < 2 and not with_current:
        click.echo(
            "Error: Provide at least two archives, or use --with-current with an archive.",
            err=True,
        )
        sys.exit(1)

    if with_current and not archives:
        click.echo("Error: Provide at least one archive when using --with-current.", err=True)
        sys.exit(1)

    if not with_current and (include_all or chat_ids):
        click.echo("Error: --all and --chat can only be used with --with-current.", err=True)
        sys.exit(1)

    if include_all and chat_ids:
        click.echo("Error: Use either --all or --chat, not both.", err=True)
        sys.exit(1)

    if output_path is None:
        output_path = "imvault_merged.imv"

    get_password = _password_source(password_fd)

    archive_inputs = []
    if separate_passwords:
        for archive in archives:
            input_password = get_password(
                f"Enter password for {os.path.basename(archive)}"
            )
            if not input_password:
                click.echo("Error: Input password cannot be empty.", err=True)
                sys.exit(1)
            archive_inputs.append((archive, input_password))
    else:
        input_password = get_password("Enter input archive password")
        if not input_password:
            click.echo("Error: Input password cannot be empty.", err=True)
            sys.exit(1)
        archive_inputs = [(archive, input_password) for archive in archives]

    output_password = get_password(
        "Enter new archive password", confirm=password_fd is None
    )
    if not output_password:
        click.echo("Error: Output password cannot be empty.", err=True)
        sys.exit(1)

    current_db = None
    current_chat_ids = []
    try:
        if with_current:
            db_path = ctx.obj["db_path"]
            resolver = _make_resolver()
            current_db = IMMessageDB(db_path, resolver=resolver)
            chats = current_db.list_chats()
            if not chats:
                click.echo("Error: No current conversations found.", err=True)
                sys.exit(1)

            if include_all:
                current_chat_ids = [c["chat_id"] for c in chats]
            elif chat_ids:
                valid_ids = {c["chat_id"] for c in chats}
                for cid in chat_ids:
                    if cid not in valid_ids:
                        click.echo(f"Warning: chat ID {cid} not found, skipping.", err=True)
                    else:
                        current_chat_ids.append(cid)
                if not current_chat_ids:
                    click.echo("Error: No valid current chat IDs provided.", err=True)
                    sys.exit(1)
            else:
                from .selector import select_chats

                current_chat_ids = select_chats(chats)
                if not current_chat_ids:
                    click.echo("No current conversations selected.")
                    return

        source_count = len(archive_inputs) + (1 if current_chat_ids else 0)
        current_label = f" plus {len(current_chat_ids)} current conversation(s)" if current_chat_ids else ""
        click.echo(f"Merging {len(archives)} archive(s){current_label}...")

        try:
            with click.progressbar(length=source_count, label="Reading sources") as bar:
                builder = MergedArchiveBuilder(
                    archive_inputs,
                    output_password,
                    output_path,
                    current_db=current_db,
                    current_chat_ids=current_chat_ids,
                    progress=lambda cur, tot: bar.update(1),
                    status=click.echo,
                )
                result_path = builder.build()
        except ValueError as e:
            click.echo(f"Error: {e}", err=True)
            sys.exit(1)
    except (FileNotFoundError, PermissionError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    finally:
        if current_db is not None:
            current_db.close()

    size = os.path.getsize(result_path)
    click.echo(
        f"Merged archive saved to {result_path} ({_format_size(size)}). "
        f"{builder.stats['messages']} messages across {builder.stats['chats']} chats; "
        f"{builder.stats['duplicates']} duplicate messages removed; "
        f"{builder.stats['attachments_written']} attachments written; "
        f"{builder.stats['attachments_deduped']} duplicate attachments removed."
    )


@cli.command("inspect")
@click.argument("archives", nargs=-1, type=click.Path(exists=True))
@click.option(
    "--separate-passwords",
    is_flag=True,
    help="Prompt for a separate password for each archive",
)
@click.option(
    "--by-chat",
    is_flag=True,
    help="Show per-chat message and attachment counts",
)
@click.option(
    "--compare-attachments",
    is_flag=True,
    help="Check that all attachments in the first archive are present in the second",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit results as a JSON object on stdout (suppresses human output).",
)
@click.option(
    "--password-fd",
    "password_fd",
    type=int,
    default=None,
    help=(
        "Read passwords from this file descriptor. With --separate-passwords, "
        "expect one password per line per archive; otherwise a single password."
    ),
)
def inspect_archives(
    archives: tuple[str, ...],
    separate_passwords: bool,
    by_chat: bool,
    compare_attachments: bool,
    as_json: bool,
    password_fd: int | None,
) -> None:
    """Inspect encrypted .imv archive message and attachment counts."""
    from .archive import archive_attachment_digests
    from .archive import inspect_archive

    if not archives:
        click.echo("Error: Provide at least one archive to inspect.", err=True)
        sys.exit(1)

    if compare_attachments and len(archives) != 2:
        click.echo("Error: --compare-attachments requires exactly two archives.", err=True)
        sys.exit(1)

    get_password = _password_source(password_fd)

    archive_inputs = []
    if separate_passwords:
        for archive in archives:
            password = get_password(f"Enter password for {os.path.basename(archive)}")
            archive_inputs.append((archive, password))
    else:
        password = get_password("Enter archive password")
        archive_inputs = [(archive, password) for archive in archives]

    archive_infos: dict[str, dict] = {}
    for archive, password in archive_inputs:
        try:
            info = inspect_archive(archive, password)
        except ValueError as e:
            if as_json:
                click.echo(json.dumps({"error": str(e), "path": archive}), err=True)
            else:
                click.echo(f"Error: {e}", err=True)
            sys.exit(1)
        archive_infos[archive] = info

        if as_json:
            continue

        click.echo(f"\n{archive}")
        click.echo(f"  Chats: {info['chat_count']}")
        click.echo(f"  Messages: {info['messages']}")
        click.echo(
            f"  Attachments: {info['attachment_present']} present "
            f"({info['unique_attachment_files']} unique files), "
            f"{info['attachment_missing']} missing metadata entries"
        )

        if by_chat:
            for chat in info["chats"]:
                click.echo(
                    f"    [{chat['chat_id']}] {chat['display_name']}: "
                    f"{chat['messages']} messages, "
                    f"{chat['attachment_present']} attachments, "
                    f"{chat['attachment_missing']} missing"
                )

    comparison = None
    if compare_attachments:
        source_archive, source_password = archive_inputs[0]
        target_archive, target_password = archive_inputs[1]
        try:
            source_total = archive_infos[source_archive]["unique_attachment_files"]
            target_total = archive_infos[target_archive]["unique_attachment_files"]
            if as_json:
                source = archive_attachment_digests(source_archive, source_password)
                target = archive_attachment_digests(target_archive, target_password)
            else:
                click.echo("\nHashing source attachments...")
                with click.progressbar(length=source_total, label="Source attachments") as bar:
                    source = archive_attachment_digests(
                        source_archive,
                        source_password,
                        progress=lambda cur, total: bar.update(1),
                    )
                click.echo("Hashing target attachments...")
                with click.progressbar(length=target_total, label="Target attachments") as bar:
                    target = archive_attachment_digests(
                        target_archive,
                        target_password,
                        progress=lambda cur, total: bar.update(1),
                    )
        except ValueError as e:
            if as_json:
                click.echo(json.dumps({"error": str(e)}), err=True)
            else:
                click.echo(f"Error: {e}", err=True)
            sys.exit(1)

        missing = source - target
        comparison = {
            "source": source_archive,
            "target": target_archive,
            "source_unique_attachments": len(source),
            "target_unique_attachments": len(target),
            "source_present_in_target": len(source) - len(missing),
            "missing_from_target": len(missing),
        }
        if not as_json:
            click.echo("\nAttachment containment")
            click.echo(f"  Source: {source_archive}")
            click.echo(f"  Target: {target_archive}")
            click.echo(f"  Source unique attachments: {len(source)}")
            click.echo(f"  Target unique attachments: {len(target)}")
            click.echo(
                f"  Source attachments present in target: {len(source) - len(missing)}"
            )
            click.echo(f"  Missing from target: {len(missing)}")

    if as_json:
        out = {"archives": [archive_infos[a] for a, _ in archive_inputs]}
        if comparison is not None:
            out["compare_attachments"] = comparison
        click.echo(json.dumps(out))


@cli.command("view")
@click.argument("archive", type=click.Path(exists=True))
@click.option(
    "--password-fd",
    "password_fd",
    type=int,
    default=None,
    help="Read archive password from this file descriptor.",
)
@click.option(
    "--no-browser",
    "no_browser",
    is_flag=True,
    help="Don't auto-launch the system browser. Useful when a GUI is embedding the URL.",
)
@click.option(
    "--progress-json",
    "progress_json",
    is_flag=True,
    help=(
        "Emit one JSON event per line on stderr (decrypt_progress, "
        "extract_progress, ready) instead of human-readable progress text. "
        "The ready event carries url and port."
    ),
)
@click.pass_context
def view_archive_cmd(
    ctx: click.Context,
    archive: str,
    password_fd: int | None,
    no_browser: bool,
    progress_json: bool,
) -> None:
    """Decrypt and view an .imv archive in the browser."""
    from .viewer import view_archive

    get_password = _password_source(password_fd)
    password = get_password("Enter archive password")

    try:
        view_archive(
            archive,
            password,
            no_browser=no_browser,
            progress_json=progress_json,
        )
    except ValueError as e:
        if progress_json:
            click.echo(json.dumps({"error": str(e), "path": archive}), err=True)
        else:
            click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    except FileNotFoundError as e:
        if progress_json:
            click.echo(json.dumps({"error": str(e), "path": archive}), err=True)
        else:
            click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@cli.command("browse")
@click.pass_context
def browse_cmd(ctx: click.Context) -> None:
    """Browse iMessage conversations directly from chat.db (no export needed)."""
    from .browser import browse_database

    db_path = ctx.obj["db_path"]
    resolver = _make_resolver()

    try:
        browse_database(db_path, resolver=resolver)
    except (FileNotFoundError, PermissionError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


def _format_size(size: int) -> str:
    """Format byte size to human-readable string."""
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


if __name__ == "__main__":
    cli()
