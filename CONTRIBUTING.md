# Contributing

Open a focused pull request from a fresh branch based on current `main`. Do not include private captures, receipts, databases, profiles, credentials, machine configuration, or production logs.

Run:

```bash
python3 tools/check_public_boundary.py
python3 -m pip install .
python3 tools/validate_contract.py
```

Behavior changes require review of the exact commit and a real production observation after merge and deployment. Never use synthetic validation as a production claim.
