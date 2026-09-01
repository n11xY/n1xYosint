# Security Policy

## Reporting a vulnerability

Please **do not** open a public issue for a security vulnerability.

Instead, use GitHub's private vulnerability reporting for this repo:
https://github.com/n11xY/n1xYosint/security/advisories/new

Include what you found, how to reproduce it, and its impact. This is a
single-maintainer project, so response time isn't guaranteed on any SLA,
but reports are read and taken seriously.

## Supported versions

There's no stable release line yet (pre-1.0) — only the latest code on
`main` is supported. Please make sure you can reproduce the issue there
before reporting.

## Scope

In scope: the `n1xYosint` codebase itself (request handling, credential/
secret handling, config parsing, plugin loading, output/export paths).

Out of scope: the third-party platforms and APIs this tool queries — a
vulnerability in GitHub, HaveIBeenPwned, etc. is theirs to fix, not ours
to report here.
