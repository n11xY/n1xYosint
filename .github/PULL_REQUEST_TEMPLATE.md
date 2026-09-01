## What does this change and why?

## Checklist

- [ ] `pytest` passes locally
- [ ] If this adds/changes a `username_sites` entry: verified against both a real, known-existing account and a nonexistent one (see [CONTRIBUTING.md](../CONTRIBUTING.md#adding-a-site-to-username_sites)) — paste the curl output or describe how you checked
- [ ] If this adds a new source module: it only calls a legitimate, documented public API or reads a page a normal browser would load (see the README's Scope section) — no auth bypass, no access-control workarounds
- [ ] If this adds a key-gated source: `is_configured()` correctly stays false without the key, and it's documented in `config/config.example.yaml`
