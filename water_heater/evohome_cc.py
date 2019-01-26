"""Support for Honeywell evohome (EMEA/EU-based systems only).

Support for a temperature control system (TCS, controller) with 0+ heating
zones (e.g. TRVs, relays) and, optionally, a DHW controller.

For more details about this platform, please refer to the documentation at
https://home-assistant.io/components/evohome/
"""

import logging

from custom_components.evohome_cc import (
    EvoBoiler,
    DATA_EVOHOME,
)

_LOGGER = logging.getLogger(__name__)


def setup_platform(hass, config, add_entities, discovery_info=None):
    """Create the DHW controller."""
    evo_data = hass.data[DATA_EVOHOME]
    entities = [evo_data['dhw']]

# 3/3: Collect any (child) DHW controller as a water_heater component
    if tcs_obj_ref.hotwater:
        _LOGGER.info(
            "setup(): Found DHW device, id: %s, type: %s",
            tcs_obj_ref.hotwater.zoneId,  # same has .dhwId
            tcs_obj_ref.hotwater.zone_type
        )
        dhw = EvoBoiler(hass, client, tcs_obj_ref.hotwater)
        evo_data['dhw'] = dhw

    parent._children = zones + [dhw]

    add_entities(entities, update_before_add=False)

    return True


class EvoDHW(EvoChildEntity, WaterHeaterDevice):
    """Base for a Honeywell evohome DHW controller (aka boiler)."""

    def __init__(self, evo_data, client, obj_ref):
        """Initialize the evohome Zone."""
        super().__init__(evo_data, client, obj_ref)

        self._config = evo_data['config'][GWS][0][TCS][0]['dhw']

        self._operation_list = ZONE_OP_LIST

        self._supported_features = \
            SUPPORT_OPERATION_MODE | \
            SUPPORT_ON_OFF

        _LOGGER.debug(
            "__init__(%s), self._config = %s",
            self._id + " [" + self._name + "]",
            self._config
        )

    @property
    def target_temperature(self):
        """Return None, as there is no target temp exposed via the api."""
        evo_data = self.hass.data[DATA_EVOHOME]

        temp = evo_data['params'][CONF_DHW_TEMP]
        temp = self.current_temperature  # a hack

        _LOGGER.warn("target_temperature(%s) = %s", self._id, temp)
        return temp

    def _set_dhw_state(self, state=None, mode=None, until=None):
        """Set the new state of a DHW controller.

        Turn the DHW on/off for an hour, until next setpoint, or indefinitely.
        The setpoint feature requires 'use_schedules' = True.

        Keyword arguments can be:
          - state  = "On" | "Off" (no default)
          - mode  = "TemporaryOverride" (default) | "PermanentOverride"
          - until.strftime('%Y-%m-%dT%H:%M:%SZ') is:
            - +1h for TemporaryOverride if not using schedules
            - next setpoint for TemporaryOverride if using schedules
            - ignored for PermanentOverride
        """
        _LOGGER.debug(
            "DHW._set_dhw_state(%s): state=%s, mode=%s, until=%s",
            self._id,
            state,
            mode,
            until
        )

        if state is None:
            state = self._status['stateStatus']['state']
        if mode is None:
            mode = EVO_TEMPOVER

        if mode != EVO_TEMPOVER:
            until = None
        else:
            if until is None:
                if self._params[CONF_USE_SCHEDULES]:
                    until = self._next_switchpoint_time
                else:
                    until = datetime.now() + timedelta(hours=1)

        if until is not None:
            until = until.strftime('%Y-%m-%dT%H:%M:%SZ')

        data = {'State': state, 'Mode': mode, 'UntilTime': until}

        _LOGGER.debug(
            "_set_dhw_state(%s): API call [1 request(s)]: dhw._set_dhw(%s)...",
            self._id,
            data
        )

        try:
            self._obj._set_dhw(data)                                            # noqa: E501; pylint: disable=protected-access

        except HTTPError as err:
            if not self._handle_exception(err):
                raise

        if self._params[CONF_USE_HEURISTICS]:
            self._status['stateStatus']['state'] = state
            self._status['stateStatus']['mode'] = mode
            self.async_schedule_update_ha_state(force_refresh=False)

    @property
    def state(self):
        """Return the state of a DHW controller.

        Reportable State can be:
          - On, working to raise current temp until equal to target temp
          - Off, current temp is ignored
          - Away, Off regardless of scheduled state
        """
        evo_data = self.hass.data[DATA_EVOHOME]

        tcs_op_mode = evo_data['status']['systemModeStatus']['mode']
        dhw_op_mode = self._status['stateStatus']['mode']

        # Determine the reported state
        dhw_state = self._status['stateStatus']['state']

        if dhw_state == DHW_STATES[STATE_ON]:
            state = STATE_ON
        elif dhw_state == DHW_STATES[STATE_OFF]:
            state = STATE_OFF
        else:
            state = STATE_UNKNOWN

        # If possible, use inheritance to override reported state
        if dhw_op_mode == EVO_FOLLOW:
            if tcs_op_mode == EVO_AWAY:
                state = EVO_AWAY  # a special form of 'Off'

        # Perform a sanity check & warn if it fails
        if state == EVO_AWAY:
            if dhw_state != DHW_STATES[STATE_OFF]:
                _LOGGER.warning(
                    "state(%s) = %s, via inheritance (via api = %s)",
                    self._id,
                    state,
                    dhw_state
                )
            else:
                _LOGGER.debug(
                    "state(%s) = %s, via inheritance (via api = %s)",
                    self._id,
                    state,
                    dhw_state
                )
        else:
            _LOGGER.debug("state(%s) = %s", self._id, state)
        return state

#   @property
#   def unit_of_measurement(self):
#       """Return the unit of measurement of this entity, if any."""
#       # this is needed for EvoBoiler(Entity) class to show a graph of temp
#       return TEMP_CELSIUS

    @property
    def is_on(self):
        """Return True if DHW is on (albeit regulated by thermostat)."""
        is_on = (self.state == DHW_STATES[STATE_ON])

        _LOGGER.debug("is_on(%s) = %s", self._id, is_on)
        return is_on

    def async_turn_on(self, mode, until):
        """Provide an async wrapper for self.turn_on().

        Note the underlying method is not asyncio-friendly.
        """
        return self.hass.async_add_job(self.turn_on, mode, until)

    def turn_on(self, mode=EVO_TEMPOVER, until=None):
        """Turn DHW on for an hour, until next setpoint, or indefinitely."""
        _LOGGER.debug(
            "turn_on(%s, mode=%s, until=%s)",
            self._id,
            mode,
            until
        )

        self._set_dhw_state(DHW_STATES[STATE_ON], mode, until)

    def async_turn_off(self, mode, until):
        """Provide an async wrapper for self.turn_off().

        Note the underlying method is not asyncio-friendly.
        """
        return self.hass.async_add_job(self.turn_off, mode, until)

    def turn_off(self, mode=EVO_TEMPOVER, until=None):
        """Turn DHW off for an hour, until next setpoint, or indefinitely."""
        _LOGGER.debug(
            "turn_off(%s, mode=%s, until=%s)",
            self._id,
            mode,
            until
        )

        self._set_dhw_state(DHW_STATES[STATE_OFF], mode, until)

    def async_set_operation_mode(self, operation_mode):
        """Provide an async wrapper for self.set_operation_mode().

        Note the underlying method is not asyncio-friendly.
        """
        return self.hass.async_add_job(self.set_operation_mode, operation_mode)

    def set_operation_mode(self, operation_mode):
        """Set new operation mode for a DHW controller."""
        _LOGGER.debug(
            "set_operation_mode(%s, operation_mode=%s)",
            self._id,
            operation_mode
        )

# FollowSchedule - return to scheduled target temp (indefinitely)
        if operation_mode == EVO_FOLLOW:
            state = ''
        else:
            state = self._status['stateStatus']['state']

# PermanentOverride - override target temp indefinitely
# TemporaryOverride - override target temp, for a period of time
        if operation_mode == EVO_TEMPOVER:
            if self._params[CONF_USE_SCHEDULES]:
                until = self._next_switchpoint_time
            else:
                until = datetime.now() + timedelta(hours=1)

            until = until.strftime('%Y-%m-%dT%H:%M:%SZ')

        else:
            until = None

        self._set_dhw_state(state, operation_mode, until)
