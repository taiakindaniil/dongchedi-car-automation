"""Allow `python -m avto_bot ...` to work the same way as the entry point."""

from .main import cli

if __name__ == "__main__":
    cli()
