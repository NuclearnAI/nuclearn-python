# nuclearn-python

Python client for the Nuclearn platform. v1 does one thing: push a pandas
DataFrame into a dataset, with chunking and upsert handled for you.

## Install

```bash
pip install git+https://github.com/NuclearnAI/nuclearn-python.git
```

## Quickstart

```python
import nuclearn

client = nuclearn.Client()  # configured from environment variables
client.upload(df, dataset_id=410, primary_key="wo_id")
```

Configuration comes from the environment (keyword arguments override):

| Variable              | Meaning                                                      |
|-----------------------|--------------------------------------------------------------|
| `NUCLEARN_API_URL`    | Base URL of your instance, e.g. `https://platform.example.com` |
| `NUCLEARN_API_KEY`    | API key (preferred — see Authentication)                     |
| `NUCLEARN_USERNAME`   | Username, if not using an API key                            |
| `NUCLEARN_PASSWORD`   | Password, if not using an API key                            |
| `NUCLEARN_VERIFY_SSL` | Set to `false` only for self-signed dev instances            |

## Authentication

**API key (preferred).** Fetch your key once (it stays valid until reset):

```bash
curl -H "Authorization: Bearer <session-token>" https://<instance>/auth/api-key
```

then set `NUCLEARN_API_KEY`. Resetting the key on the platform immediately
revokes the old one.

**Username / password.** Set `NUCLEARN_USERNAME` and `NUCLEARN_PASSWORD`; the
client performs the login flow and transparently re-authenticates when the
session token expires. Use a dedicated service account for unattended
integrations, not a person's login — that keeps revocation and audit clean.
Note that repeated failed logins temporarily lock the account.

## Upload semantics

```python
client.upload(df, dataset_id=410, primary_key="wo_id", chunk_size=1000)
```

- Each DataFrame row becomes one record. `NaN`/`NaT` become nulls, datetimes
  become ISO-8601 strings.
- With `primary_key`, rows **upsert** by that column: re-pushing the same key
  updates the record in place instead of appending. Null or duplicate key
  values in the DataFrame are rejected before anything is sent.
- Without `primary_key`, rows are keyed by a hash of their content, so
  re-running the same push is idempotent (and identical rows collapse into
  one record).
- Large frames are sent in `chunk_size`-row requests; the call returns an
  `UploadResult(dataset_id, records_sent, chunks)` once every chunk is
  accepted.

## More advanced functionality

The full function reference — health/auth diagnostics, queue depths, and
everything else beyond uploading — lives in [docs/api.md](docs/api.md).

## Errors

All errors derive from `nuclearn.NuclearnError`:

- `ConfigurationError` — missing URL or credentials
- `AuthenticationError` — the platform rejected the key or login
- `UploadError` — a request was rejected (carries `.status_code`)

## Development

```bash
python -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest tests/test_client.py          # unit tests, fully offline
```

The integration suite runs against a live platform and is skipped unless
`NUCLEARN_API_URL` is set; it creates and deletes its own scratch dataset:

```bash
NUCLEARN_API_URL=https://localhost:8000 \
NUCLEARN_USERNAME=... NUCLEARN_PASSWORD=... \
NUCLEARN_VERIFY_SSL=false \
.venv/bin/pytest tests/test_integration.py
```
