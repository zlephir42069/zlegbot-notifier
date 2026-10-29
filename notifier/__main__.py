"""Allow `python -m notifier`."""

import sys

from notifier.cli import main

if __name__ == "__main__":
    sys.exit(main())
