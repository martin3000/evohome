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
    EvoBoiler,

    DATA_EVOHOME,
    CONF_LOCATION_IDX,
)

_LOGGER = logging.getLogger(__name__)


def setup_platform(hass, config, add_entities, discovery_info=None):
    """Create a Honeywell (EMEA/EU) evohome CH/DHW system.

    An evohome system consists of: a controller, with 0-12 heating zones (e.g.
    TRVs, relays) and, optionally, a DHW controller (a HW boiler).

    Here, we add the controller, and the zones (if there are any).
    """
    evo_data = hass.data[DATA_EVOHOME]

    client = evo_data['client']
    loc_idx = evo_data['params'][CONF_LOCATION_IDX]

# 1/3: Collect the (parent) controller - evohomeclient has no defined way of
# accessing non-default location other than using the protected member
    tcs_obj_ref = client.locations[loc_idx]._gateways[0]._control_systems[0]    # noqa E501; pylint: disable=protected-access

    _LOGGER.info(
        "setup_platform(): Found Controller, id: %s, type: %s, idx=%s, loc=%s",
        tcs_obj_ref.systemId + " [" + tcs_obj_ref.location.name + "]",
        tcs_obj_ref.modelType,
        loc_idx,
        tcs_obj_ref.location.locationId,
    )
    parent = EvoController(hass, client, tcs_obj_ref)
    parent._children = children = []
    parent._dhw = dhw = []

# 2/3: Collect each (child) Heating zone as a (climate component) device
    for zone_obj_ref in tcs_obj_ref._zones:                                     # noqa E501; pylint: disable=protected-access
        _LOGGER.info(
            "setup_platform(): Found Zone device, id: %s, type: %s",
            zone_obj_ref.zoneId + " [" + zone_obj_ref.name + "]",
            zone_obj_ref.zone_type  # also has .zoneType (different)
        )
# We may not handle some zones correctly (e.g. UFH) - how to test for them?
#       if zone['zoneType'] in [ "RadiatorZone", "ZoneValves" ]:
        children.append(EvoZone(hass, client, zone_obj_ref))

    parent._zones = children

# 3/3: Collect any (child) DHW zone as a (climate component) device
    if tcs_obj_ref.hotwater:
        _LOGGER.info(
            "setup_platform(): Found DHW device, id: %s, type: %s",
            tcs_obj_ref.hotwater.zoneId,  # also has .dhwId (same)
            tcs_obj_ref.hotwater.zone_type
        )
        parent._dhw = EvoBoiler(hass, client, tcs_obj_ref.hotwater)
        children.append(parent._dhw)

    parent._children = children


# for efficiency, add parent (controller) + all children in a single call
    add_entities([parent] + children, update_before_add=False)

    return True
