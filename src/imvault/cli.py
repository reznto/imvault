"""Click CLI for imvault: list, export, view commands."""

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
    """imvault — Archive iMessage conversations into encrypted .imv files."""
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


@cli.command("list")
@click.pass_context
def list_chats(ctx: click.Context) -> None:
    """List all iMessage conversations."""
    from .db import IMMessageDB

    db_path = ctx.obj["db_path"]
    resolver = _make_resolver()
    try:
        db = IMMessageDB(db_path, resolver=resolver)
    except (FileNotFoundError, PermissionError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    with db:
        chats = db.list_chats()

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
@click.pass_context
def export_chats(
    ctx: click.Context,
    export_all: bool,
    chat_ids: tuple[int, ...],
    output_path: str | None,
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

        password = click.prompt("Enter archive password", hide_input=True, confirmation_prompt=True)

        if not password:
            click.echo("Error: Password cannot be empty.", err=True)
            sys.exit(1)

        click.echo(f"Exporting {len(selected_ids)} conversation(s)...")

        with click.progressbar(length=len(selected_ids), label="Exporting conversations") as bar:
            builder = ArchiveBuilder(db, password, output_path, selected_ids,
                                     progress=lambda cur, tot: bar.update(1))
            result_path = builder.build()
        click.echo("Encrypting archive...")

    size = os.path.getsize(result_path)
    click.echo(f"Archive saved to {result_path} ({_format_size(size)})")


@cli.command("view")
@click.argument("archive", type=click.Path(exists=True))
@click.pass_context
def view_archive_cmd(ctx: click.Context, archive: str) -> None:
    """Decrypt and view an .imv archive in the browser."""
    from .viewer import view_archive

    password = click.prompt("Enter archive password", hide_input=True)

    try:
        view_archive(archive, password)
    except ValueError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    except FileNotFoundError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


def _format_size(size: int) -> str:
    """Format byte size to human-readable string."""
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"
