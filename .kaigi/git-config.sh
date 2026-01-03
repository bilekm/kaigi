#!/bin/bash
# Wrapper for git commits with kaigi as author
# This makes "kaigi" show up as a contributor on GitHub

KAIGI_NAME="kaigi"
KAIGI_EMAIL="kaigi@users.noreply.github.com"  # Or use a real email

# Run git commit with kaigi as author
git -C /mnt/data/workspace/kaigi \
  -c user.name="$KAIGI_NAME" \
  -c user.email="$KAIGI_EMAIL" \
  "$@"
