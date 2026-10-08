# API reference

The complete public surface. For install and a two-line start, see the
[README](../README.md).

## `Client`

```python
nuclearn.Client(api_url=None, api_key=None, username=None, password=None,
                verify=None, timeout=60.0)
```

Authenticated connection to a platform instance. Every argument falls back to
its environment variable (`NUCLEARN_API_URL`, `NUCLEARN_API_KEY`,
`NUCLEARN_USERNAME`, `NUCLEARN_PASSWORD`, `NUCLEARN_VERIFY_SSL`); keyword
arguments win. Raises `ConfigurationError` if the URL or credentials are
missing. With an API key, requests carry it directly; with username/password,
the client performs the login flow lazily, caches the session token, and
re-authenticates once on expiry.

## `Client.upload`

```python
client.upload(df, dataset_id, primary_key=None, chunk_size=1000) -> UploadResult
```

Upserts a pandas DataFrame into a dataset. Each row becomes one record;
`NaN`/`NaT` become nulls, datetimes become ISO-8601 strings. Rows are sent in
`chunk_size`-row requests.

- With `primary_key`, rows upsert by that column: re-pushing the same key
  updates in place instead of appending. Null or duplicate key values are
  rejected client-side before anything is sent.
- Without it, rows are keyed by a hash of their content, so re-running the
  same push is idempotent (identical rows collapse into one record).

Returns `UploadResult(dataset_id, records_sent, chunks)`. Raises
`UploadError` (carries `.status_code`) if the platform rejects a chunk.

## Troubleshooting checks

```python
client.connection_check(verbose=False) -> bool
client.auth_check(verbose=False) -> bool
```

Both return plain `True`/`False` and print nothing unless `verbose=True`,
which prints the reason for a failure.

- `connection_check()` hits the instance's `/health` endpoint without
  authenticating, so it separates network/firewall problems from credential
  problems.
- `auth_check()` makes a real authenticated request (performing the login
  flow in username/password mode), so a `True` means uploads will be
  accepted. Verbose output distinguishes *unreachable* from *rejected*.

Module-level variants configure themselves from the environment and never
raise — missing configuration is a `False` with a verbose explanation — so
they work as one-liners on a customer box:

```bash
python -c "import nuclearn; nuclearn.connection_check(verbose=True); nuclearn.auth_check(verbose=True)"
```

## Queue depths

```python
client.queue_counts() -> dict[str, int]
client.queue_count(queue_name) -> int
client.background_queue_count() -> int   # sugar — works for every queue name
```

`queue_counts()` returns the waiting-message count for every Celery queue on
the instance, keyed by queue name — useful for checking whether a push is
stuck behind a processing backlog.

Per-queue methods like `background_queue_count()` resolve dynamically against
the live queue list, so new platform queues work without a library update.
Asking for a queue that doesn't exist raises a `NuclearnError` naming the
queues that do:

```
No queue named 'system_queue'; available: background_queue, general_queue,
inference_queue, jobs_queue, monitoring_queue, ocr_image_queue, ocr_queue,
ocr_realtime_queue, realtime_queue
```

Also available module-level: `nuclearn.queue_counts()`.

## Errors

All errors derive from `nuclearn.NuclearnError`:

| Exception             | Raised when                                        |
|-----------------------|----------------------------------------------------|
| `ConfigurationError`  | missing URL or credentials at `Client()` time      |
| `AuthenticationError` | the platform rejected the key or login             |
| `UploadError`         | a request was rejected (carries `.status_code`)    |
