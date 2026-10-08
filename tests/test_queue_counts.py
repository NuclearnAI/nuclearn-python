"""Unit tests for queue_counts and the per-queue count sugar — HTTP mocked."""

import pytest
import responses

import nuclearn
from nuclearn import Client, NuclearnError

API_URL = "https://platform.example.com"
OVERVIEW_URL = f"{API_URL}/tasks/monitoring/overview"

OVERVIEW_PAYLOAD = {
    "workers": {},
    "queues": {
        "summary": {"total_waiting": 7, "queue_count": 3},
        "details": [
            {"name": "background_queue", "waiting": 5, "consumers": 1},
            {"name": "general_queue", "waiting": 2, "consumers": 2},
            {"name": "jobs_queue", "waiting": 0, "consumers": 1},
        ],
    },
    "tasks": {},
    "health": {},
}


def make_client():
    return Client(api_url=API_URL, api_key="test-key")


def add_overview(mocked):
    mocked.add(responses.GET, OVERVIEW_URL, json=OVERVIEW_PAYLOAD)


class TestQueueCounts:
    @responses.activate
    def test_returns_dict_of_ints_keyed_by_queue_name(self):
        add_overview(responses)
        counts = make_client().queue_counts()
        assert counts == {"background_queue": 5, "general_queue": 2, "jobs_queue": 0}
        assert all(isinstance(v, int) for v in counts.values())

    @responses.activate
    def test_non_200_raises_nuclearn_error_with_detail(self):
        responses.add(
            responses.GET, OVERVIEW_URL, status=503, json={"detail": "broker down"}
        )
        with pytest.raises(NuclearnError, match="broker down"):
            make_client().queue_counts()

    @responses.activate
    def test_module_level_reads_env(self, monkeypatch):
        monkeypatch.setenv("NUCLEARN_API_URL", API_URL)
        monkeypatch.setenv("NUCLEARN_API_KEY", "env-key")
        add_overview(responses)
        assert nuclearn.queue_counts()["background_queue"] == 5


class TestSingleQueueCounts:
    @responses.activate
    def test_queue_count_by_name(self):
        add_overview(responses)
        assert make_client().queue_count("background_queue") == 5

    @responses.activate
    def test_unknown_queue_raises_and_names_available_queues(self):
        add_overview(responses)
        with pytest.raises(NuclearnError, match="background_queue"):
            make_client().queue_count("system_queue")

    @responses.activate
    def test_per_queue_methods_resolve_dynamically(self):
        add_overview(responses)
        add_overview(responses)
        client = make_client()
        assert client.background_queue_count() == 5
        assert client.jobs_queue_count() == 0

    @responses.activate
    def test_nonexistent_queue_method_raises_at_call_not_lookup(self):
        add_overview(responses)
        client = make_client()
        probe = client.system_queue_count  # lookup succeeds (pattern matches)
        with pytest.raises(NuclearnError, match="system_queue"):
            probe()

    def test_other_missing_attributes_still_raise_attribute_error(self):
        with pytest.raises(AttributeError):
            make_client().definitely_not_a_method
