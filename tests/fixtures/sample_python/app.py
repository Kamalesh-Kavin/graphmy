"""
Sample Python source file used as a test fixture.

This file exercises the Python parser for:
  - Module-level functions (sync and async)
  - Classes with methods
  - Decorators
  - Docstrings
  - Inheritance
  - Import statements
  - Function calls
"""

import os  # noqa: F401 — intentionally imported to test IMPORTS edge detection
import sys  # noqa: F401 — intentionally imported to test IMPORTS edge detection

# ---------------------------------------------------------------------------
# Top-level functions
# ---------------------------------------------------------------------------


def greet(name: str) -> str:
    """Return a greeting message."""
    return f"Hello, {name}!"


async def fetch_data(url: str, timeout: int = 30) -> dict:
    """
    Fetch data from a URL asynchronously.

    Parameters
    ----------
    url : str
        The URL to fetch.
    timeout : int
        Request timeout in seconds.
    """
    result = greet("world")
    return {"url": url, "result": result}


def _private_helper(value: int) -> int:
    """Internal helper. Should still be indexed."""
    return value * 2


# ---------------------------------------------------------------------------
# Classes
# ---------------------------------------------------------------------------


class Animal:
    """Base class for all animals."""

    def __init__(self, name: str, age: int) -> None:
        self.name = name
        self.age = age

    def speak(self) -> str:
        """Make the animal speak."""
        raise NotImplementedError

    def describe(self) -> str:
        """Return a human-readable description."""
        return f"{self.name} (age {self.age})"


class Dog(Animal):
    """A dog. Inherits from Animal."""

    def __init__(self, name: str, age: int, breed: str) -> None:
        super().__init__(name, age)
        self.breed = breed

    def speak(self) -> str:
        return f"{self.name} says: Woof!"

    def fetch(self, item: str) -> str:
        """Fetch an item."""
        return f"{self.name} fetched {item}"


# ---------------------------------------------------------------------------
# Decorated functions
# ---------------------------------------------------------------------------


def my_decorator(func):
    """A simple decorator."""

    def wrapper(*args, **kwargs):
        return func(*args, **kwargs)

    return wrapper


@my_decorator
def decorated_function(x: int) -> int:
    """A decorated function."""
    result = _private_helper(x)
    return result
