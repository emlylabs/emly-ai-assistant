# Security policy

## Reporting a vulnerability

Email **security@emlylabs.com**. Please don't open a public GitHub issue, pull request or discussion for a suspected vulnerability.

Include as much of this as you can:

- what is affected (component, endpoint, version or commit, and whether it's the Docker image or a source install),
- how to reproduce it, step by step,
- what an attacker could do with it,
- any logs, requests or proof-of-concept code (please redact real personal data and secrets).

If you'd like to encrypt your report, say so in a first short email and we'll arrange a way to do that.

## What to expect

| Step | Target |
|------|--------|
| We confirm we received your report | within 3 business days |
| We tell you whether we accept it and how severe we think it is | within 10 business days |
| Fix released for a critical or high severity issue | within 30 days of confirming it |
| Fix released for a medium or low severity issue | in a regular release, usually within 90 days |

We'll keep you updated while we work on it, tell you before we publish a fix, and credit you in the release notes unless you'd rather stay anonymous. If a deadline slips, we'll tell you why and give you a new date.

## Supported versions

Security fixes are made on the `main` branch and shipped in the next release. Only the latest release gets fixes; we don't backport to older releases. Until the first tagged release, the current `main` is the only supported version.

Release images include an SPDX software bill of materials (SBOM), attached to each GitHub release.

## Scope

In scope: the code in this repository (the API server, admin UI, embeddable widget, channel integrations and the Docker image built from the `Dockerfile`).

Out of scope:

- vulnerabilities in third-party services you connect the app to (LLM providers, identity providers, Slack/Teams/WhatsApp/Telegram, SMTP relays). Report those to the vendor.
- findings that need an already-compromised server, admin account or host,
- deployments that ignore the production settings in the README (for example, running with `MODE=dev`, publishing Qdrant or Postgres ports, or allowing `*` as a widget origin),
- missing hardening with no demonstrated impact, automated scanner output without a working exploit, and denial of service by sheer traffic volume.

## Testing guidelines

Please test only against your own installation. Don't access, change or delete other people's data, don't degrade a service others rely on, and give us a reasonable chance to fix the issue before you disclose it. If you act in good faith within these guidelines, we won't pursue legal action over your research.
