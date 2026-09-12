# Security Policy

## API keys

This public repository and its releases intentionally contain no API keys. The portable launcher asks for an OpenCode Go key on first run and stores it locally under `userdata/.local/share/opencode/auth.json`.

Never commit or attach `auth.json`, `.env`, screenshots containing keys, or populated portable runtime data. If a key is exposed, revoke it in the OpenCode console before opening a report.

The Windows setup script writes `auth.json` atomically and requires an NTFS volume to enforce a user-only ACL. FAT/exFAT and some network or synchronized volumes may not support the required ACL; setup fails closed instead of retaining a key without that protection.

## Reporting a vulnerability

Please use GitHub private vulnerability reporting when available. Do not put exploitable details or credentials in a public issue.
