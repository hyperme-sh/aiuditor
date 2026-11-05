"""Owner serializers match frozen consumer contracts in source and installed packages."""
import json
from pathlib import Path

import pytest
from controlplane_events.contracts import event_schema
from event_schema_projection import generated_schema

SOURCES = json.loads(Path(__file__).with_name("event_contract_sources.json").read_text())["events"]


@pytest.mark.parametrize("event", sorted(SOURCES))
def test_every_owner_payload_projection_matches_its_versioned_schema(event):
    expected = generated_schema(event, SOURCES[event])
    assert event_schema(event) == expected, f"{event}: payload changed without a new schema version"
