# Advanced functionality

Everything beyond `Client` + `upload()`. The quickstart, configuration, and
upload semantics live in the [README](../README.md).

## Troubleshooting checks

Two diagnostics, both returning `True`/`False` — silent unless `verbose=True`,
which prints what failed:

```python
client.connection_check()   # can the instance be reached at all? (no auth)
client.auth_check()         # are the configured credentials accepted?
```

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
client.queue_counts()             # {"background_queue": 5, "general_queue": 0, ...}
client.queue_count("jobs_queue")  # 0
client.background_queue_count()   # sugar — works for every queue name
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
