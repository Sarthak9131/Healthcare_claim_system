"""Local conversation simulator that calls the real webhook contract."""

import asyncio

from fastapi.testclient import TestClient

from claims_processing_cx.api import create_app
from claims_processing_cx.models import Claim
from claims_processing_cx.storage import InMemoryStorage
from claims_processing_cx.synthetic import create_synthetic_dataset


def main() -> None:
    """Simulate an identity-verified status lookup without printing identifiers."""
    dataset = create_synthetic_dataset()
    storage = InMemoryStorage(dataset)
    claim = next(
        item for item in dataset.claims if "expired_coverage" in item.scenario_tags
    )
    member = storage.get_member(claim.member_id or "")
    if member is None:
        raise RuntimeError("The synthetic conversation fixture has no member.")
    app = create_app(storage)
    pipeline = app.state.pipeline
    claim_payload = Claim.model_validate(claim).model_dump(mode="json")
    asyncio.run(pipeline.adjudicate(claim_payload))

    with TestClient(app) as client:
        response = client.post(
            "/webhook",
            json={
                "fulfillmentInfo": {"tag": "claim_status"},
                "sessionInfo": {
                    "parameters": {
                        "member_id": member.member_id,
                        "date_of_birth": member.date_of_birth.isoformat(),
                        "claim_id": claim.claim_id,
                    }
                },
            },
        )
    if response.status_code != 200:
        raise RuntimeError(f"Local CX webhook failed with HTTP {response.status_code}.")
    message = response.json()["fulfillmentResponse"]["messages"][0]["text"]["text"][0]
    print("Synthetic user: Check my claim status.")
    print("Synthetic agent: Identity verified using fixture values (not displayed).")
    print(f"Synthetic agent: {message}")


if __name__ == "__main__":
    main()
