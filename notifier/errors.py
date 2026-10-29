"""Error types that map onto the CLI exit codes."""


class ConfigError(Exception):
    """Invalid config or CLI arguments. Exit code 2."""

    def __init__(self, messages):
        if isinstance(messages, str):
            messages = [messages]
        self.messages = [str(item) for item in messages if str(item)]
        super().__init__("\n".join(self.messages))


class PostError(Exception):
    """A single post failed. Other items still run. The id is not recorded."""


class StateError(Exception):
    """State could not be read or written, or the lock timed out."""
