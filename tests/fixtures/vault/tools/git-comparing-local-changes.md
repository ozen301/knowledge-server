# Git - Comparing Local Changes

To see changes relative to the remote branch:

    git diff origin/main

This is useful when a machine contains modifications that haven't been pushed.

Important distinction:

- `git diff` → changes in files
- `git log` → commit history
- `git diff A..B` → difference between two commits/branches

If the changes were committed locally, inspect the commits individually
rather than generating one large diff.

Related: [[Git Basics]]
