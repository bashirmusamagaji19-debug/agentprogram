"""Temporary scaffold CLI for the web task agent."""

import argparse


def main(argv: list[str] | None = None) -> int:
    """Run the temporary scaffold CLI."""
    parser = argparse.ArgumentParser(
        prog="web-task-agent",
        description="Scaffold CLI for the web task agent.",
    )
    parser.add_argument("--keyword")
    parser.add_argument("--location", default="Remote")
    parser.add_argument("--target-count", type=int, default=10)
    args = parser.parse_args(argv)

    print("Web task agent scaffold: real workflow is not implemented yet.")
    print(f"keyword: {args.keyword or '(not provided)'}")
    print(f"location: {args.location}")
    print(f"target_count: {args.target_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
