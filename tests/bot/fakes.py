"""Reusable Discord doubles and registered-command invocation helpers."""

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from bot.errors import handle_app_command_error


@dataclass(slots=True)
class FakeRole:
    id: int


@dataclass(slots=True)
class FakeGuild:
    id: int = 100
    name: str = "Test Guild"


@dataclass(slots=True)
class FakeDiscordMember:
    id: int = 200
    roles: list[FakeRole] = field(default_factory=list)
    administrator: bool = False

    @property
    def guild_permissions(self) -> Any:
        return SimpleNamespace(administrator=self.administrator)


class FakeResponse:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self.deferrals: list[dict[str, Any]] = []
        self.edits: list[dict[str, Any]] = []
        self.modals: list[Any] = []
        self._done = False

    def is_done(self) -> bool:
        return self._done

    async def defer(self, *, ephemeral: bool = False) -> None:
        if self._done:
            raise AssertionError("Interaction response was acknowledged twice")
        self._done = True
        self.deferrals.append({"ephemeral": ephemeral})

    async def send_message(
        self, content: str | None = None, *, ephemeral: bool = False, view: Any = None
    ) -> None:
        if self._done:
            raise AssertionError("Interaction response was acknowledged twice")
        self._done = True
        message = {"content": content, "ephemeral": ephemeral}
        if view is not None:
            message["view"] = view
        self.messages.append(message)

    async def edit_message(self, **kwargs: Any) -> None:
        if self._done:
            raise AssertionError("Interaction response was acknowledged twice")
        self._done = True
        self.edits.append(kwargs)

    async def send_modal(self, modal: Any) -> None:
        if self._done:
            raise AssertionError("Interaction response was acknowledged twice")
        self._done = True
        self.modals.append(modal)


class FakeFollowup:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    async def send(
        self, content: str | None = None, *, ephemeral: bool = False, view: Any = None
    ) -> None:
        message = {"content": content, "ephemeral": ephemeral}
        if view is not None:
            message["view"] = view
        self.messages.append(message)


class FakeInteraction:
    def __init__(
        self,
        client: Any,
        *,
        guild: FakeGuild | None = None,
        user: FakeDiscordMember | None = None,
    ) -> None:
        self.client = client
        self.guild = guild if guild is not None else FakeGuild()
        self.user = user if user is not None else FakeDiscordMember()
        self.response = FakeResponse()
        self.followup = FakeFollowup()

    @property
    def messages(self) -> list[dict[str, Any]]:
        return self.response.messages + self.followup.messages


class FakeAttachment:
    def __init__(
        self,
        content: bytes,
        *,
        filename: str = "data.json",
        size: int | None = None,
        content_type: str | None = "application/json",
    ) -> None:
        self.filename = filename
        self.size = len(content) if size is None else size
        self.content_type = content_type
        self.content = content
        self.read_called = False

    async def read(self) -> bytes:
        self.read_called = True
        return self.content


@dataclass(slots=True)
class FakeCommandContext:
    interaction: FakeInteraction

    @property
    def guild(self) -> FakeGuild | None:
        return self.interaction.guild

    @property
    def author(self) -> FakeDiscordMember:
        return self.interaction.user


def registered_command(cog: Any, name: str) -> Any:
    groups = cog.__cog_app_commands__
    return next(command for group in groups for command in group.commands if command.name == name)


async def invoke_registered(
    cog: Any,
    name: str,
    interaction: FakeInteraction,
    *args: Any,
    handle_errors: bool = True,
    **kwargs: Any,
) -> Any:
    """Run a registered callback with the checks Discord would run before it."""
    command = registered_command(cog, name)
    try:
        for check in command.checks:
            await check(interaction)
        return await command.callback(cog, interaction, *args, **kwargs)
    except Exception as error:
        if not handle_errors:
            raise
        await handle_app_command_error(interaction, error)
        return None
