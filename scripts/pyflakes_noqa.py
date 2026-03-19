"""pyflakes wrapper that suppresses lines containing '# noqa'."""

import subprocess
import sys


def main():
    args = sys.argv[1:] if len(sys.argv) > 1 else ["."]
    result = subprocess.run(
        [sys.executable, "-m", "pyflakes"] + args,
        capture_output=True,
        text=True,
    )

    violations = []
    for line in result.stdout.splitlines():
        # pyflakes format: filename:lineno:col: message
        parts = line.split(":")
        if len(parts) < 3:
            violations.append(line)
            continue
        filename = parts[0]
        try:
            lineno = int(parts[1]) - 1
        except ValueError:
            violations.append(line)
            continue
        try:
            with open(filename, encoding="utf-8") as fh:
                source_line = fh.readlines()[lineno]
        except (OSError, IndexError):
            violations.append(line)
            continue
        if "# noqa" not in source_line:
            violations.append(line)

    if violations:
        print("\n".join(violations))
        sys.exit(1)


if __name__ == "__main__":
    main()
