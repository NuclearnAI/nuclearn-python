"""Client for the Nuclearn platform API."""

import hashlib
import json
import os
import re
from dataclasses import dataclass
from functools import partial
from typing import Any, Callable, Dict, List, Optional

import pandas as pd
import requests
import urllib3

from .errors import AuthenticationError, ConfigurationError, NuclearnError, UploadError

_FALSE_STRINGS = {"0", "false", "no", "off"}
_QUEUE_COUNT_ATTRIBUTE = re.compile(r"^(\w+_queue)_count$")


@dataclass
class UploadResult:
    """Summary of a completed upload."""

    dataset_id: int
    records_sent: int
    chunks: int


class Client:
    """Authenticated connection to a Nuclearn platform instance.

    Configuration falls back to environment variables:
    NUCLEARN_API_URL, NUCLEARN_API_KEY, NUCLEARN_USERNAME,
    NUCLEARN_PASSWORD, NUCLEARN_VERIFY_SSL.
    """

    def __init__(
        self,
        api_url: Optional[str] = None,
        api_key: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        verify: Optional[bool] = None,
        timeout: float = 60.0,
    ):
        self.api_url = self._resolve_api_url(api_url)
        self._api_key = api_key or os.getenv("NUCLEARN_API_KEY")
        self._username = username or os.getenv("NUCLEARN_USERNAME")
        self._password = password or os.getenv("NUCLEARN_PASSWORD")
        self._validate_credentials()
        self.verify = self._resolve_verify(verify)
        if not self.verify:
            # The user explicitly opted out of TLS verification (self-signed
            # dev instances); without this every request emits a warning.
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        self.timeout = timeout
        self._session = requests.Session()
        self._access_token: Optional[str] = None

    # --- configuration ---

    @staticmethod
    def _resolve_api_url(api_url: Optional[str]) -> str:
        url = api_url or os.getenv("NUCLEARN_API_URL")
        if not url:
            raise ConfigurationError(
                "No API URL configured. Pass api_url= or set NUCLEARN_API_URL."
            )
        return url.rstrip("/")

    def _validate_credentials(self) -> None:
        if self._api_key:
            return
        if self._username and self._password:
            return
        raise ConfigurationError(
            "No credentials configured. Set NUCLEARN_API_KEY (preferred), or "
            "both NUCLEARN_USERNAME and NUCLEARN_PASSWORD, or pass the "
            "equivalent keyword arguments."
        )

    @staticmethod
    def _resolve_verify(verify: Optional[bool]) -> bool:
        if verify is not None:
            return verify
        env = os.getenv("NUCLEARN_VERIFY_SSL")
        if env is not None and env.strip().lower() in _FALSE_STRINGS:
            return False
        return True

    # --- auth ---

    def _auth_headers(self) -> Dict[str, str]:
        if self._api_key:
            return {"X-API-KEY": self._api_key}
        if self._access_token is None:
            self._access_token = self._login()
        return {"Authorization": f"Bearer {self._access_token}"}

    def _fetch_csrf_token(self) -> str:
        response = self._session.get(
            f"{self.api_url}/auth/csrf-token",
            timeout=self.timeout,
            verify=self.verify,
        )
        response.raise_for_status()
        return response.json()["csrf_token"]

    def _login(self) -> str:
        response = self._session.post(
            f"{self.api_url}/auth/login",
            json={"username": self._username, "password": self._password},
            headers={"X-CSRF-Token": self._fetch_csrf_token()},
            timeout=self.timeout,
            verify=self.verify,
        )
        if response.status_code in (401, 403):
            raise AuthenticationError(
                "Login failed: the platform rejected the username/password. "
                "Note: repeated failures temporarily lock the account."
            )
        response.raise_for_status()
        return response.json()["access_token"]

    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        """Issue an authenticated request, re-logging in once on token expiry."""
        response = self._send(method, path, **kwargs)
        if response.status_code == 401 and not self._api_key:
            self._access_token = None
            response = self._send(method, path, **kwargs)
        if response.status_code == 401:
            raise AuthenticationError(
                "The platform rejected the configured credentials (401)."
            )
        return response

    def _send(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        return self._session.request(
            method,
            f"{self.api_url}{path}",
            headers=self._auth_headers(),
            timeout=self.timeout,
            verify=self.verify,
            **kwargs,
        )

    # --- health checks ---

    def connection_check(self, verbose: bool = False) -> bool:
        """True if the instance's /health endpoint answers. No auth involved."""
        return _check_connection(self.api_url, self.verify, self.timeout, verbose)

    def auth_check(self, verbose: bool = False) -> bool:
        """True if the configured credentials are accepted by the platform."""
        try:
            response = self._request("GET", "/auth/rules")
        except AuthenticationError as exc:
            return _check_failed(verbose, "auth_check", str(exc))
        except requests.RequestException as exc:
            return _check_failed(
                verbose, "auth_check", f"could not reach {self.api_url}: {exc}"
            )
        if response.status_code != 200:
            return _check_failed(
                verbose,
                "auth_check",
                f"{self.api_url} answered {response.status_code}: "
                f"{_error_detail(response)}",
            )
        _diagnose(verbose, f"auth_check: OK — credentials accepted by {self.api_url}")
        return True

    # --- queue depths ---

    def queue_counts(self) -> Dict[str, int]:
        """Waiting-message count per Celery queue, keyed by queue name."""
        response = self._request("GET", "/tasks/monitoring/overview")
        if response.status_code != 200:
            raise NuclearnError(
                f"queue_counts failed ({response.status_code}): "
                f"{_error_detail(response)}"
            )
        details = response.json()["queues"]["details"]
        return {queue["name"]: int(queue["waiting"]) for queue in details}

    def queue_count(self, queue: str) -> int:
        """Waiting-message count for one queue by name."""
        counts = self.queue_counts()
        if queue not in counts:
            raise NuclearnError(
                f"No queue named {queue!r}; available: {', '.join(sorted(counts))}"
            )
        return counts[queue]

    def __getattr__(self, name: str) -> Callable[[], int]:
        """Resolve <queue_name>_count() methods against the live queue list."""
        match = _QUEUE_COUNT_ATTRIBUTE.match(name)
        if match:
            return partial(self.queue_count, match.group(1))
        raise AttributeError(
            f"{type(self).__name__!r} object has no attribute {name!r}"
        )

    # --- upload ---

    def upload(
        self,
        df: pd.DataFrame,
        dataset_id: int,
        primary_key: Optional[str] = None,
        chunk_size: int = 1000,
    ) -> UploadResult:
        """Upsert a DataFrame into a dataset.

        Each row becomes one record. With primary_key, rows upsert by that
        column's value; without it, by a hash of the row's content (so
        re-uploading the same rows is idempotent).
        """
        records = _dataframe_to_records(df, primary_key)
        chunks = _split_into_chunks(records, chunk_size)
        for chunk in chunks:
            self._upsert_chunk(dataset_id, chunk)
        return UploadResult(
            dataset_id=dataset_id, records_sent=len(records), chunks=len(chunks)
        )

    def _upsert_chunk(self, dataset_id: int, chunk: List[Dict[str, Any]]) -> None:
        response = self._request(
            "PUT",
            f"/datasets/{dataset_id}/upsert-source-multirecords",
            json={"data": chunk},
        )
        if response.status_code != 200:
            raise UploadError(
                f"Upload to dataset {dataset_id} failed "
                f"({response.status_code}): {_error_detail(response)}",
                status_code=response.status_code,
            )


def connection_check(
    api_url: Optional[str] = None,
    verify: Optional[bool] = None,
    timeout: float = 10.0,
    verbose: bool = False,
) -> bool:
    """Module-level convenience: reachability check needing no credentials."""
    try:
        url = Client._resolve_api_url(api_url)
    except ConfigurationError as exc:
        return _check_failed(verbose, "connection_check", str(exc))
    return _check_connection(url, Client._resolve_verify(verify), timeout, verbose)


def auth_check(verbose: bool = False, **client_kwargs: Any) -> bool:
    """Module-level convenience: build a Client from env/kwargs and check auth."""
    try:
        client = Client(**client_kwargs)
    except ConfigurationError as exc:
        return _check_failed(verbose, "auth_check", str(exc))
    return client.auth_check(verbose=verbose)


def queue_counts(**client_kwargs: Any) -> Dict[str, int]:
    """Module-level convenience: build a Client from env/kwargs, return counts."""
    return Client(**client_kwargs).queue_counts()


def _check_connection(api_url: str, verify: bool, timeout: float, verbose: bool) -> bool:
    if not verify:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    try:
        response = requests.get(f"{api_url}/health", timeout=timeout, verify=verify)
    except requests.RequestException as exc:
        return _check_failed(
            verbose, "connection_check", f"could not reach {api_url}/health: {exc}"
        )
    if response.status_code != 200:
        return _check_failed(
            verbose,
            "connection_check",
            f"{api_url}/health answered {response.status_code}",
        )
    _diagnose(verbose, f"connection_check: OK — {api_url}/health answered 200")
    return True


def _diagnose(verbose: bool, message: str) -> None:
    if verbose:
        print(message)


def _check_failed(verbose: bool, check_name: str, reason: str) -> bool:
    _diagnose(verbose, f"{check_name}: FAILED — {reason}")
    return False


def _dataframe_to_records(
    df: pd.DataFrame, primary_key: Optional[str]
) -> List[Dict[str, Any]]:
    # to_json handles NaN/NaT -> null and datetimes -> ISO strings.
    rows = json.loads(df.to_json(orient="records", date_format="iso"))
    if primary_key is not None:
        uids = _primary_key_uids(df, primary_key)
        return [{"data": row, "source_uid": uid} for row, uid in zip(rows, uids)]
    return _content_hashed_records(rows)


def _primary_key_uids(df: pd.DataFrame, primary_key: str) -> List[str]:
    if primary_key not in df.columns:
        raise UploadError(f"primary_key column {primary_key!r} is not in the DataFrame")
    values = df[primary_key]
    if values.isna().any():
        raise UploadError(f"primary_key column {primary_key!r} contains null values")
    uids = [str(value) for value in values]
    if len(set(uids)) != len(uids):
        raise UploadError(f"primary_key column {primary_key!r} contains duplicate values")
    return uids


def _content_hashed_records(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Key each row by a hash of its content; identical rows collapse to one."""
    records: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        digest = hashlib.sha256(
            json.dumps(row, sort_keys=True, default=str).encode()
        ).hexdigest()
        records[digest] = {"data": row, "source_uid": digest}
    return list(records.values())


def _split_into_chunks(
    records: List[Dict[str, Any]], chunk_size: int
) -> List[List[Dict[str, Any]]]:
    return [records[i : i + chunk_size] for i in range(0, len(records), chunk_size)]


def _error_detail(response: requests.Response) -> str:
    try:
        return str(response.json().get("detail", response.text))
    except ValueError:
        return response.text
