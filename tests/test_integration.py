"""Live integration test against a running Nuclearn platform.

Skipped unless the environment is configured:

    NUCLEARN_API_URL=https://localhost:8000 \
    NUCLEARN_USERNAME=... NUCLEARN_PASSWORD=... \
    NUCLEARN_VERIFY_SSL=false \
    pytest tests/test_integration.py

Creates a scratch dataset, pushes a DataFrame through the public client
surface, reads it back over the API, proves upsert semantics, and deletes
the scratch dataset. No credentials live in this file.
"""

import os
import uuid

import pandas as pd
import pytest

from nuclearn import Client

pytestmark = pytest.mark.skipif(
    not os.getenv("NUCLEARN_API_URL"),
    reason="integration test: set NUCLEARN_API_URL and credentials",
)


@pytest.fixture(scope="module")
def client():
    return Client()


@pytest.fixture()
def scratch_dataset(client):
    name = f"nuclearn-python-inttest-{uuid.uuid4().hex[:8]}"
    response = client._request("PUT", "/datasets/create-dataset", json={"name": name})
    assert response.status_code == 200, response.text
    dataset_id = response.json()["id"]
    yield dataset_id
    client._request("DELETE", f"/datasets/delete-dataset/{dataset_id}")


def fetch_records_by_uid(client, dataset_id):
    response = client._request(
        "GET",
        f"/datasets/{dataset_id}/records",
        params={"record_idx": 0, "num_recs_per_page": 100},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    records = payload["data"] if isinstance(payload, dict) else payload
    return {rec["source_uid"]: rec["data"] for rec in records}


def sample_dataframe():
    return pd.DataFrame(
        {
            "wo_id": [101, 102, 103],
            "title": ["replace seal", "inspect pump", "lube bearing"],
            "hours": [4.5, None, 2.0],
            "due": pd.to_datetime(["2026-11-01", "2026-11-15", None]),
        }
    )


def test_upload_roundtrip_and_upsert(client, scratch_dataset):
    df = sample_dataframe()

    result = client.upload(df, dataset_id=scratch_dataset, primary_key="wo_id")
    assert result.records_sent == 3

    records = fetch_records_by_uid(client, scratch_dataset)
    assert set(records) == {"101", "102", "103"}
    assert records["101"]["title"] == "replace seal"
    assert records["102"]["hours"] is None
    assert records["101"]["due"].startswith("2026-11-01")

    # Same primary keys, one changed value: must update in place, not append.
    df.loc[df.wo_id == 101, "title"] = "replace seal (expedited)"
    client.upload(df, dataset_id=scratch_dataset, primary_key="wo_id")

    records = fetch_records_by_uid(client, scratch_dataset)
    assert len(records) == 3
    assert records["101"]["title"] == "replace seal (expedited)"


def test_upload_with_api_key(client, scratch_dataset):
    response = client._request("GET", "/auth/api-key")
    assert response.status_code == 200, response.text
    api_key = response.json()["access_token"]

    key_client = Client(api_key=api_key, verify=client.verify)
    result = key_client.upload(
        pd.DataFrame({"wo_id": [201], "title": ["via api key"]}),
        dataset_id=scratch_dataset,
        primary_key="wo_id",
    )
    assert result.records_sent == 1

    records = fetch_records_by_uid(client, scratch_dataset)
    assert records["201"]["title"] == "via api key"
