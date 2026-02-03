"""Interactive chat selector using InquirerPy."""

from typing import Any


def select_chats(chats: list[dict[str, Any]]) -> list[int]:
    """Display an interactive checkbox prompt for chat selection.

    Returns a list of selected chat_id values.
    """
    from InquirerPy import inquirer

    if not chats:
        return []

    choices = [
        {"name": "Select All", "value": "__all__"},
    ]
    for chat in chats:
        name = chat["display_name"]
        count = chat["message_count"]
        n_parts = len(chat.get("participants", []))
        last = chat.get("last_date", "")
        if last:
            last = last[:10]  # date portion only

        parts_label = f", {n_parts} people" if n_parts > 1 else ""
        label = f"{name} ({count} msgs{parts_label}, last: {last})"

        choices.append({"name": label, "value": chat["chat_id"]})

    selected = inquirer.fuzzy(
        message="Search and select conversations to export:",
        choices=choices,
        multiselect=True,
        instruction="(Type to filter | Tab: toggle | Shift+Tab: toggle+up | Ctrl+A: all | Enter: confirm)",
        validate=lambda result: len(result) > 0 or "Select at least one conversation.",
    ).execute()

    if "__all__" in selected:
        return [c["chat_id"] for c in chats]

    return selected
