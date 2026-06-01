"""Compatibility wrapper for the archived source-slice extractor."""

from legacy.extract_source_slice import *  # noqa: F401,F403


if __name__ == "__main__":
    from legacy.extract_source_slice import main

    main()
