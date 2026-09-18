# Support

## Where to go

| What you need | Where |
| --- | --- |
| How do I do X? | [Discussions](https://github.com/anubhav-n-mishra/Automated-ai-insight-system/discussions) |
| Something is broken | [Bug report](https://github.com/anubhav-n-mishra/Automated-ai-insight-system/issues/new?template=bug_report.yml) |
| A figure looks wrong | [Correctness report](https://github.com/anubhav-n-mishra/Automated-ai-insight-system/issues/new?template=wrong_number.yml) |
| A security problem | [Private advisory](https://github.com/anubhav-n-mishra/Automated-ai-insight-system/security/advisories/new). Not a public issue |
| An idea | [Feature request](https://github.com/anubhav-n-mishra/Automated-ai-insight-system/issues/new?template=feature_request.yml) |

## Before you ask

These resolve most questions faster than waiting for a reply:

```sh
insight-engine doctor                    # effective configuration, no secrets
insight-engine validate your-report.yaml # what is wrong with a specification
insight-engine profile your-data.csv     # what the engine thinks your columns are
```

The documentation:

- [README](README.md) — install, first report, configuration
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — how it works and why
- [docs/CONFIGURATION.md](docs/CONFIGURATION.md) — every specification field
- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) — running it in production
- [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) — the failures people hit

## What to include

A report that includes these is usually answered in one round instead of four:

1. The output of `insight-engine doctor`
2. The specification, with credentials removed
3. A handful of representative rows — **synthetic, not real customer data**
4. What you expected, and what you got
5. The `request_id` from the response, if this came through the API

## Response times

This is a volunteer-maintained project. Expect a first response within about a
week; security reports are acknowledged within 3 working days. See
[MAINTAINERS.md](MAINTAINERS.md) for the full table.

Nudging a thread after a week is welcome.

## Commercial support

None is offered. The Apache-2.0 licence permits anyone to offer it, and nobody
needs permission to do so.
