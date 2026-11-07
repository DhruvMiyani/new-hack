# Local security lab

These are fictional identities and declarative configuration for Blindspot's
executable local object-storage / access-control harness. No real credentials
or customer data are present. This is **not** an AWS IAM emulator, Terraform
scanner, or a live cloud assessment. Passing proves only the frozen local
contract checked by this harness.

`policy/requirements.json` and `identity/` define the independent requirements
and authoritative identities. `infra/storage.json` and `service/access.json`
are the candidate configuration. A proposed repair may change only the latter
two files, in memory; tests, identities, and requirements stay frozen.

Run from the repository root:

```bash
.venv/bin/python verify.py security --offline
.venv/bin/python verify.py security --scenario public-storage --offline --repair
.venv/bin/python verify.py security --scenario privilege-escalation --offline --repair
.venv/bin/python verify.py security --scenario environment-leak --offline --repair
.venv/bin/python verify.py security --scenario clean --offline
```

The default scenario combines all four defects. Omit `--offline` and pass
`--models` to ask SubQ to rank risk across all supplied artifacts and Meta to
propose a configuration repair. Unavailable models are reported explicitly;
only executed checks determine the verdict. `--offline --repair` uses a labelled
deterministic repair instead. Neither mode writes to cloud accounts.
