"""Private local stdio transport, managed natively by the MCP client."""

from .server import create_server


def main():
    create_server().run()


if __name__ == "__main__":
    main()
