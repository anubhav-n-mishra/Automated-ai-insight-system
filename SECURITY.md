# Security Policy

## Supported versions

Security fixes land on the latest minor release. Older minors receive fixes for
critical issues for 90 days after the next minor ships.

| Version | Supported          |
| ------- | ------------------ |
| 1.x     | Yes                |
| < 1.0   | No                 |

## Reporting a vulnerability

**Do not open a public issue for a security problem.**

Report privately through
[GitHub Security Advisories](https://github.com/anubhav-n-mishra/Automated-ai-insight-system/security/advisories/new).
If that is not available to you, email the maintainer address listed in
[MAINTAINERS.md](MAINTAINERS.md) with `SECURITY` in the subject.

Please include:

- what an attacker can do, and what access they need to start
- steps to reproduce, ideally the smallest case that shows it
- affected versions and configuration
- any proposed fix

### What to expect

| Stage | Target |
| ----- | ------ |
| Acknowledgement of your report | 3 working days |
| Initial assessment and severity | 10 working days |
| Fix released, or a public plan with a date | 90 days |

We will keep you updated at least every 14 days while a report is open. If we
cannot fix an issue within 90 days we will say so publicly and explain why,
rather than let it sit silently.

We follow coordinated disclosure. We ask that you give us the 90 days before
publishing. We will credit you in the advisory and the changelog unless you
would rather we did not.

### Safe harbour

We will not pursue or support legal action against anyone who reports a
vulnerability in good faith, stays within the scope below, avoids privacy
violations and service degradation, and gives us reasonable time to respond.

### Out of scope

- Findings that require an attacker to already control the deployment's
  configuration, environment variables or filesystem
- Denial of service through resource exhaustion on a deployment that has
  disabled the shipped rate limit and upload caps
- Reports generated solely by an automated scanner, with no demonstrated impact
- Vulnerabilities in a dependency, unless this project's use of it is what
  makes it exploitable — report those upstream first
- Missing hardening headers on `/health`, which is deliberately minimal
- Social engineering, physical attacks, or attacks on the maintainers

## What this software promises

An operator can reasonably expect the following. A failure of any of these is a
vulnerability, and reporting one is welcome.

**Access control.** A dashboard session is reachable only with its access token.
Tokens are 256 bits of CSPRNG output, stored only as a SHA-256 hash, and
compared in constant time. A missing token is a failure, never a skipped check.

**Isolation of artifacts.** Generated decks, audio, staged uploads and session
records are served only through handlers that validate their key. No directory
containing session state is ever mounted as static content.

**Input containment.** Uploaded files are written under server-generated
identifiers, never under a client-supplied filename. Derived-metric expressions
are parsed against an allowlisted grammar and never evaluated as code.
Specification documents are validated in full before any data is read.

**Outbound restraint.** The engine makes no network connection a deployment has
not enabled. Remote database sources are off by default; enabling them requires
both an explicit opt-in and a host allowlist.

**Error hygiene.** Responses never contain stack traces, filesystem paths,
connection strings or provider payloads. Those go to the log, correlated by
request id.

**Secret handling.** Credentials are read from the environment, held as
`SecretStr`, redacted from logs, and never written to a generated artifact or a
session record.

## What this software does not promise

Knowing the boundary matters as much as knowing the guarantees.

- **It is not a multi-tenant data platform.** API keys authenticate a caller;
  they do not partition data. Anyone with a valid key can generate a report from
  any source the deployment can reach. Run one deployment per trust boundary.
- **Dashboard links are bearer credentials.** Anyone holding the link can read
  that report until it expires. Treat a share link like a password.
- **The narrative is model output.** When a provider is configured, the prose is
  generated. The figures are computed and passed to it, but the sentences are
  not verified. Do not rely on generated prose for a regulated disclosure.
- **The default rate limiter is per-process.** It bounds a single node. A
  multi-node deployment needs a shared limiter at the ingress.
- **SQL read-only checks are not a security boundary.** They catch accidents.
  Use a read-only database role.

## Hardening a deployment

Running with `INSIGHT_ENGINE_ENVIRONMENT=production` refuses to start unless
authentication and a public base URL are configured, debug is off, CORS is not a
wildcard, and any enabled remote SQL access carries a host allowlist.

Beyond that:

- Terminate TLS in front of the service; share links carry tokens.
- Mount the data directory as the only writable path; the shipped container
  already runs read-only, non-root, with all capabilities dropped.
- Give any database user read-only access to the specific tables you report on.
- Put a shared rate limiter at the ingress if you run more than one replica.
- Scrape `/metrics` and alert on `insight_engine_jobs_total{outcome="failed"}`.
- Rotate API keys by adding the new one, deploying, then removing the old one:
  `INSIGHT_ENGINE_API_KEYS` accepts several at once for exactly this.

## Security in the development process

- Every pull request runs `ruff` security rules, strict type checking, the test
  suite, `pip-audit` against the dependency tree, secret scanning and CodeQL.
- Dependencies are updated weekly by Dependabot; security updates are not
  batched with routine ones.
- Releases are published with build provenance attestation and through PyPI
  trusted publishing, so no long-lived token exists in this repository.
- The test suite contains explicit regression tests for every vulnerability
  class that has been fixed, so a fix cannot be quietly undone.
