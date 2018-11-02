"""Support for Honeywell evohome (EMEA/EU-based systems only).

Support for a temperature control system (TCS, controller) with 0+ heating
zones (e.g. TRVs, relays) and, optionally, a DHW controller.

For more details about this platform, please refer to the documentation at
https://home-assistant.io/components/evohome/
"""

import logging

from custom_components.evohome import (
    EvoController,
    EvoZone,
    DATA_EVOHOME,
)

_LOGGER = logging.getLogger(__name__)


def setup_platform(hass, config, add_entities, discovery_info=None):
    """Create the controller, and its zones (if there are any)."""
    evo_data = hass.data[DATA_EVOHOME]
    entities = [evo_data['parent']] + evo_data['zones']

    add_entities(entities, update_before_add=False)

    return True
