"""
Support for Honeywell Evohome (EU): a controller with 0+ zones +/- DHW.
"""

from custom_components.evohome import (
    evoController, 
    evoZone, 
    evoDhwSensor,
    evoDhwSwitch,
    
    DATA_EVOHOME, 
    CONF_LOCATION_IDX,
)

import logging

_LOGGER = logging.getLogger(__name__)


def setup_platform(hass, config, add_devices, discovery_info=None):
    """Set up a Honeywell evohome CH/DHW system."""

    _LOGGER.debug("Started: setup_platform()")

# Pull out the domain configuration from hass.data
    ec_api = hass.data[DATA_EVOHOME]['evohomeClient']
    ec_idx = hass.data[DATA_EVOHOME]['config'][CONF_LOCATION_IDX]
    ec_loc = ec_api.installation_info[ec_idx]


# 1/3: Collect the (master) controller
    tcsObjRef = ec_api.locations[ec_idx]._gateways[0]._control_systems[0]

    _LOGGER.info(
        "Found Controller object [idx=%s]: id: %s [%s], type: %s",
        ec_idx,
        tcsObjRef.systemId,
        tcsObjRef.location.name,
        tcsObjRef.modelType
    )

# 1/3: Collect each (slave) zone as a (climate component) device
    slaves = []

    for zoneObjRef in tcsObjRef._zones:
        _LOGGER.info(
            "Found Zone object: id: %s, type: %s",
            zoneObjRef.zoneId + " [" + zoneObjRef.name + " ]",
            zoneObjRef.zoneType
        )

# We may not handle some zones correctly (e.g. UFH) - how to test for them?
#       if zone['zoneType'] in [ "RadiatorZone", "ZoneValves" ]:
        slaves.append(evoZone(hass, ec_api, zoneObjRef))


# 2/3: Collect any (slave) DHW zone as a (climate component) device
    if tcsObjRef.hotwater:
        _LOGGER.info(
            "Found DHW object: dhwId: %s, zoneId: %s, type: %s",
            tcsObjRef.hotwater.dhwId,
            tcsObjRef.hotwater.zoneId,
            tcsObjRef.hotwater.zone_type
        )
        
        slaves.append(evoDhwSensor(hass, ec_api, tcsObjRef.hotwater))
        slaves.append(evoDhwSwitch(hass, ec_api, tcsObjRef.hotwater))

        
    master = evoController(hass, ec_api, tcsObjRef, slaves)
    
# Now, for efficiency) add controller and all zones in a single call
    add_devices([master] + slaves, False)

    _LOGGER.debug("Finished: setup_platform()")
    return True
