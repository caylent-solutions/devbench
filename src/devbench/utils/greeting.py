"""Greeting utility for poc-verification pipeline exercises."""


def get_greeting(name: str) -> str:
    """Return a greeting string for the given name.

    Args:
        name: The name to greet.

    Returns:
        A greeting of the form ``"Hello, <name>!"``.
    """
    return f"Hello, {name}!"
