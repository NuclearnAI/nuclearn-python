"""Unit tests for connection_check / auth_check — HTTP mocked, nothing sensitive."""

import pandas as pd
import pytest
import responses
from requests.exceptions import ConnectionError as RequestsConnectionError

import nuclearn
from nuclearn import Client

API_URL = "https://platform.example.com"
HEALTH_URL = f"{API_URL}/health"
RULES_URL = f"{API_URL}/auth/rules"


def make_client(**kwargs):
    kwargs.setdefault("api_url", API_URL)
    kwargs.setdefault("api_key", "test-key")
    return Client(**kwargs)


def clear_nuclearn_env(monkeypatch):
    for var in (
        "NUCLEARN_API_URL",
        "NUCLEARN_API_KEY",
        "NUCLEARN_USERNAME",
        "NUCLEARN_PASSWORD",
    ):
        monkeypatch.delenv(var, raising=False)


class TestConnectionCheck:
    @responses.activate
    def test_true_when_health_answers_200(self):
        responses.add(responses.GET, HEALTH_URL, json={"status": "ok"})
        assert make_client().connection_check() is True

    @responses.activate
    def test_false_when_health_answers_503(self):
        responses.add(responses.GET, HEALTH_URL, status=503)
        assert make_client().connection_check() is False

    @responses.activate
    def test_false_when_unreachable(self):
        responses.add(responses.GET, HEALTH_URL, body=RequestsConnectionError("refused"))
        assert make_client().connection_check() is False

    @responses.activate
    def test_silent_by_default_verbose_prints_diagnosis(self, capsys):
        responses.add(responses.GET, HEALTH_URL, body=RequestsConnectionError("refused"))
        responses.add(responses.GET, HEALTH_URL, body=RequestsConnectionError("refused"))

        client = make_client()
        client.connection_check()
        assert capsys.readouterr().out == ""

        client.connection_check(verbose=True)
        out = capsys.readouterr().out
        assert "FAILED" in out
        assert API_URL in out

    @responses.activate
    def test_module_level_needs_no_credentials(self, monkeypatch):
        clear_nuclearn_env(monkeypatch)
        responses.add(responses.GET, HEALTH_URL, json={"status": "ok"})
        assert nuclearn.connection_check(api_url=API_URL) is True

    def test_module_level_missing_url_is_false_not_raise(self, monkeypatch, capsys):
        clear_nuclearn_env(monkeypatch)
        assert nuclearn.connection_check(verbose=True) is False
        assert "NUCLEARN_API_URL" in capsys.readouterr().out


class TestAuthCheck:
    @responses.activate
    def test_true_when_rules_answer_200(self):
        responses.add(responses.GET, RULES_URL, json=[])
        assert make_client().auth_check() is True

    @responses.activate
    def test_false_on_rejected_api_key(self):
        responses.add(responses.GET, RULES_URL, status=401)
        assert make_client().auth_check() is False

    @responses.activate
    def test_false_on_failed_login(self):
        responses.add(
            responses.GET, f"{API_URL}/auth/csrf-token", json={"csrf_token": "c"}
        )
        responses.add(responses.POST, f"{API_URL}/auth/login", status=401)
        client = Client(api_url=API_URL, username="u@example.com", password="bad")
        assert client.auth_check() is False

    @responses.activate
    def test_false_when_unreachable_and_verbose_says_reach_not_reject(self, capsys):
        responses.add(responses.GET, RULES_URL, body=RequestsConnectionError("refused"))
        assert make_client().auth_check(verbose=True) is False
        assert "reach" in capsys.readouterr().out

    @responses.activate
    def test_silent_by_default_verbose_prints_ok(self, capsys):
        responses.add(responses.GET, RULES_URL, json=[])
        responses.add(responses.GET, RULES_URL, json=[])

        client = make_client()
        client.auth_check()
        assert capsys.readouterr().out == ""

        client.auth_check(verbose=True)
        assert "OK" in capsys.readouterr().out

    @responses.activate
    def test_module_level_reads_env(self, monkeypatch):
        clear_nuclearn_env(monkeypatch)
        monkeypatch.setenv("NUCLEARN_API_URL", API_URL)
        monkeypatch.setenv("NUCLEARN_API_KEY", "env-key")
        responses.add(responses.GET, RULES_URL, json=[])
        assert nuclearn.auth_check() is True

    def test_module_level_missing_credentials_is_false_not_raise(
        self, monkeypatch, capsys
    ):
        clear_nuclearn_env(monkeypatch)
        monkeypatch.setenv("NUCLEARN_API_URL", API_URL)
        assert nuclearn.auth_check(verbose=True) is False
        assert "credentials" in capsys.readouterr().out.lower()
