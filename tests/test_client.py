"""Unit tests for nuclearn.Client — all HTTP is mocked, nothing sensitive."""

import json

import pandas as pd
import pytest
import responses

from nuclearn import (
    AuthenticationError,
    Client,
    ConfigurationError,
    UploadError,
)

API_URL = "https://platform.example.com"
UPSERT_URL = f"{API_URL}/datasets/42/upsert-source-multirecords"


def make_client(**kwargs):
    kwargs.setdefault("api_url", API_URL)
    kwargs.setdefault("api_key", "test-key")
    return Client(**kwargs)


def upsert_response(request):
    body = json.loads(request.body)
    return (200, {}, json.dumps({"data": body["data"]}))


def add_upsert_endpoint(mocked):
    mocked.add_callback(responses.PUT, UPSERT_URL, callback=upsert_response)


# --- configuration ---------------------------------------------------------


class TestConfiguration:
    def test_missing_api_url_raises(self, monkeypatch):
        monkeypatch.delenv("NUCLEARN_API_URL", raising=False)
        with pytest.raises(ConfigurationError, match="NUCLEARN_API_URL"):
            Client(api_key="k")

    def test_missing_credentials_raises(self, monkeypatch):
        for var in ("NUCLEARN_API_KEY", "NUCLEARN_USERNAME", "NUCLEARN_PASSWORD"):
            monkeypatch.delenv(var, raising=False)
        with pytest.raises(ConfigurationError, match="NUCLEARN_API_KEY"):
            Client(api_url=API_URL)

    def test_password_without_username_raises(self, monkeypatch):
        monkeypatch.delenv("NUCLEARN_API_KEY", raising=False)
        monkeypatch.delenv("NUCLEARN_USERNAME", raising=False)
        with pytest.raises(ConfigurationError):
            Client(api_url=API_URL, password="pw")

    def test_env_fallbacks(self, monkeypatch):
        monkeypatch.setenv("NUCLEARN_API_URL", API_URL)
        monkeypatch.setenv("NUCLEARN_API_KEY", "env-key")
        client = Client()
        assert client.api_url == API_URL

    def test_kwargs_override_env(self, monkeypatch):
        monkeypatch.setenv("NUCLEARN_API_URL", "https://wrong.example.com")
        client = make_client()
        assert client.api_url == API_URL

    def test_trailing_slash_stripped(self):
        client = make_client(api_url=API_URL + "/")
        assert client.api_url == API_URL


# --- auth ------------------------------------------------------------------


class TestApiKeyAuth:
    @responses.activate
    def test_api_key_sent_as_header(self):
        add_upsert_endpoint(responses)
        make_client().upload(pd.DataFrame({"a": [1]}), dataset_id=42)
        assert responses.calls[0].request.headers["X-API-KEY"] == "test-key"

    @responses.activate
    def test_api_key_rejected_raises_authentication_error(self):
        responses.add(responses.PUT, UPSERT_URL, status=401)
        with pytest.raises(AuthenticationError):
            make_client().upload(pd.DataFrame({"a": [1]}), dataset_id=42)


class TestPasswordAuth:
    def add_login_endpoints(self, mocked, access_token="tok-1"):
        mocked.add(
            responses.GET,
            f"{API_URL}/auth/csrf-token",
            json={"csrf_token": "csrf-abc"},
            headers={"Set-Cookie": "csrf_token=csrf-abc; Path=/"},
        )
        mocked.add(
            responses.POST,
            f"{API_URL}/auth/login",
            json={"access_token": access_token},
        )

    def password_client(self):
        return Client(api_url=API_URL, username="svc@example.com", password="pw")

    @responses.activate
    def test_login_flow_and_bearer_header(self):
        self.add_login_endpoints(responses)
        add_upsert_endpoint(responses)

        self.password_client().upload(pd.DataFrame({"a": [1]}), dataset_id=42)

        login = responses.calls[1].request
        assert login.headers["X-CSRF-Token"] == "csrf-abc"
        assert json.loads(login.body) == {
            "username": "svc@example.com",
            "password": "pw",
        }
        upsert = responses.calls[2].request
        assert upsert.headers["Authorization"] == "Bearer tok-1"

    @responses.activate
    def test_token_cached_across_uploads(self):
        self.add_login_endpoints(responses)
        add_upsert_endpoint(responses)

        client = self.password_client()
        client.upload(pd.DataFrame({"a": [1]}), dataset_id=42)
        client.upload(pd.DataFrame({"a": [2]}), dataset_id=42)

        login_calls = [c for c in responses.calls if c.request.url.endswith("/auth/login")]
        assert len(login_calls) == 1

    @responses.activate
    def test_expired_token_triggers_one_relogin(self):
        self.add_login_endpoints(responses, access_token="tok-1")
        responses.add(responses.PUT, UPSERT_URL, status=401)
        self.add_login_endpoints(responses, access_token="tok-2")
        add_upsert_endpoint(responses)

        self.password_client().upload(pd.DataFrame({"a": [1]}), dataset_id=42)

        final = responses.calls[-1].request
        assert final.headers["Authorization"] == "Bearer tok-2"

    @responses.activate
    def test_bad_password_raises_authentication_error(self):
        responses.add(
            responses.GET,
            f"{API_URL}/auth/csrf-token",
            json={"csrf_token": "csrf-abc"},
        )
        responses.add(responses.POST, f"{API_URL}/auth/login", status=401)
        with pytest.raises(AuthenticationError):
            self.password_client().upload(pd.DataFrame({"a": [1]}), dataset_id=42)


# --- upload ----------------------------------------------------------------


class TestUpload:
    @responses.activate
    def test_records_payload_round_trips_values(self):
        add_upsert_endpoint(responses)
        df = pd.DataFrame(
            {
                "name": ["pump", "valve"],
                "count": [3, None],
                "when": pd.to_datetime(["2026-01-02", None]),
            }
        )

        make_client().upload(df, dataset_id=42)

        body = json.loads(responses.calls[0].request.body)
        rows = [rec["data"] for rec in body["data"]]
        assert rows[0]["name"] == "pump"
        assert rows[0]["when"].startswith("2026-01-02")
        assert rows[1]["count"] is None
        assert rows[1]["when"] is None

    @responses.activate
    def test_chunking_splits_requests(self):
        add_upsert_endpoint(responses)
        df = pd.DataFrame({"a": range(2500)})

        result = make_client().upload(df, dataset_id=42, chunk_size=1000)

        assert len(responses.calls) == 3
        sizes = [len(json.loads(c.request.body)["data"]) for c in responses.calls]
        assert sizes == [1000, 1000, 500]
        assert result.records_sent == 2500
        assert result.chunks == 3

    @responses.activate
    def test_primary_key_becomes_source_uid(self):
        add_upsert_endpoint(responses)
        df = pd.DataFrame({"wo_id": [101, 102], "status": ["open", "closed"]})

        make_client().upload(df, dataset_id=42, primary_key="wo_id")

        body = json.loads(responses.calls[0].request.body)
        assert [rec["source_uid"] for rec in body["data"]] == ["101", "102"]

    def test_unknown_primary_key_raises(self):
        df = pd.DataFrame({"a": [1]})
        with pytest.raises(UploadError, match="wo_id"):
            make_client().upload(df, dataset_id=42, primary_key="wo_id")

    def test_duplicate_primary_key_raises(self):
        df = pd.DataFrame({"wo_id": [101, 101]})
        with pytest.raises(UploadError, match="duplicate"):
            make_client().upload(df, dataset_id=42, primary_key="wo_id")

    def test_null_primary_key_raises(self):
        df = pd.DataFrame({"wo_id": [101, None]})
        with pytest.raises(UploadError, match="null"):
            make_client().upload(df, dataset_id=42, primary_key="wo_id")

    @responses.activate
    def test_without_primary_key_identical_rows_collapse(self):
        add_upsert_endpoint(responses)
        df = pd.DataFrame({"a": [1, 1, 2]})

        result = make_client().upload(df, dataset_id=42)

        body = json.loads(responses.calls[0].request.body)
        assert len(body["data"]) == 2
        uids = [rec["source_uid"] for rec in body["data"]]
        assert len(set(uids)) == 2
        assert result.records_sent == 2

    @responses.activate
    def test_hash_source_uid_is_stable_across_uploads(self):
        add_upsert_endpoint(responses)
        client = make_client()
        df = pd.DataFrame({"a": [1], "b": ["x"]})

        client.upload(df, dataset_id=42)
        client.upload(df, dataset_id=42)

        first = json.loads(responses.calls[0].request.body)["data"][0]["source_uid"]
        second = json.loads(responses.calls[1].request.body)["data"][0]["source_uid"]
        assert first == second

    @responses.activate
    def test_empty_dataframe_sends_nothing(self):
        result = make_client().upload(pd.DataFrame({"a": []}), dataset_id=42)
        assert result.records_sent == 0
        assert result.chunks == 0
        assert len(responses.calls) == 0

    @responses.activate
    def test_server_error_raises_upload_error_with_detail(self):
        responses.add(
            responses.PUT,
            UPSERT_URL,
            status=404,
            json={"detail": "Dataset with id 42 not found."},
        )
        with pytest.raises(UploadError, match="Dataset with id 42 not found"):
            make_client().upload(pd.DataFrame({"a": [1]}), dataset_id=42)
