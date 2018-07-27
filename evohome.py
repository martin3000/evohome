"""
Support for Honeywell Evohome (EU): a controller with 0+ zones +/- DHW.

To install it, copy it to ${HASS_CONFIG_DIR}/custom_components. The
configuration.yaml as below.  scan_interval is in seconds, but is rounded up to
nearest minute.

evohome:
  username: !secret_evohome_username
  password: !secret_evohome_password
  scan_interval: 300
"""

# TBD
# re: https://developers.home-assistant.io/docs/en/development_index.html
#  - checked with: flake8 --ignore=E303,E241 --max-line-length=150 evohome.py
#  - OAUTH_TIMEOUT_SECS to be config var

import functools as ft
import logging
import requests
import sched
import socket
import voluptuous as vol

from datetime import datetime, timedelta
from time import sleep, strftime, strptime, mktime

from homeassistant.components.climate import (
    ClimateDevice, PLATFORM_SCHEMA,

#   SERVICE_SET_OPERATION_MODE = 'set_operation_mode'
#   SERVICE_SET_TEMPERATURE = 'set_temperature'
#   SERVICE_SET_AWAY_MODE = 'set_away_mode'

    SUPPORT_TARGET_TEMPERATURE,
    SUPPORT_TARGET_TEMPERATURE_HIGH,
    SUPPORT_TARGET_TEMPERATURE_LOW,
    SUPPORT_OPERATION_MODE,
    SUPPORT_AWAY_MODE,
    SUPPORT_ON_OFF,

    ATTR_CURRENT_TEMPERATURE,
    ATTR_MAX_TEMP,
    ATTR_MIN_TEMP,
    ATTR_TARGET_TEMP_HIGH,
    ATTR_TARGET_TEMP_LOW,
    ATTR_TARGET_TEMP_STEP,
    ATTR_OPERATION_MODE,
    ATTR_OPERATION_LIST,
    ATTR_AWAY_MODE,
)

# these are specific to this component
ATTR_UNTIL='until'

from homeassistant.components.switch import (
  SwitchDevice
)

from homeassistant.const import (
#   ATTR_ASSUMED_STATE = 'assumed_state',
#   ATTR_STATE = 'state',
#   ATTR_SUPPORTED_FEATURES = 'supported_features'
#   ATTR_TEMPERATURE = 'temperature'
    ATTR_TEMPERATURE,

    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    
    DEVICE_CLASS_TEMPERATURE,

    EVENT_HOMEASSISTANT_START,

    PRECISION_WHOLE,
    PRECISION_HALVES,
    PRECISION_TENTHS,

    STATE_OFF,
    STATE_ON,

#   TEMP_FAHRENHEIT,
    TEMP_CELSIUS,
)

# these are specific to this component
CONF_HIGH_PRECISION = 'high_precision'
CONF_USE_HEURISTICS = 'use_heuristics'
CONF_USE_SCHEDULES = 'use_schedules'
CONF_LOCATION_IDX = 'location_idx'
CONF_AWAY_TEMP = 'away_temp_is'
CONF_OFF_TEMP = 'off_temp_is'

from homeassistant.core                import callback
from homeassistant.helpers.discovery   import load_platform
from homeassistant.helpers.temperature import display_temp as show_temp
from homeassistant.helpers.entity      import Entity, ToggleEntity
from homeassistant.helpers.event       import track_state_change
from homeassistant.loader              import bind_hass

import homeassistant.helpers.config_validation as cv
# from homeassistant.helpers.config_validation import PLATFORM_SCHEMA  # noqa

## TBD: for testing only (has extra logging)
# https://www.home-assistant.io/developers/component_deps_and_reqs/
# https://github.com/home-assistant/home-assistant.github.io/pull/5199

##TBD: these vars for >=0.2.6 (is it v3 of the api?)
#REQUIREMENTS = ['https://github.com/zxdavb/evohome-client/archive/master.zip#evohomeclient==0.2.7'] # noqa
REQUIREMENTS = ['https://github.com/zxdavb/evohome-client/archive/logging.zip#evohomeclient==0.2.7'] # noqa
SETPOINT_CAPABILITIES = 'setpointCapabilities'
SETPOINT_STATUS       = 'setpointStatus'
TARGET_TEMPERATURE    = 'targetHeatTemperature'
OAUTH_TIMEOUT_SECS    = 21600  ## timeout is 6h, client has an oauth workaround

## these vars for <=0.2.5...
#REQUIREMENTS = ['evohomeclient==0.2.5']
#SETPOINT_CAPABILITIES = 'heatSetpointCapabilities'
#SETPOINT_STATUS       = 'heatSetpointStatus'
#TARGET_TEMPERATURE    = 'targetTemperature'
#OAUTH_TIMEOUT_SECS    = 3600  ## timeout is 60 mins

## https://www.home-assistant.io/components/logger/
_LOGGER = logging.getLogger(__name__)

DOMAIN='evohome'
DATA_EVOHOME = 'data_evohome'
DISPATCHER_EVOHOME = 'dispatcher_evohome'

# Validation of the user's configuration.
CONFIG_SCHEMA = vol.Schema({
    DOMAIN: vol.Schema({
        vol.Required(CONF_USERNAME): cv.string,
        vol.Required(CONF_PASSWORD): cv.string,
        vol.Optional(CONF_SCAN_INTERVAL, default=180): cv.positive_int,

        vol.Optional(CONF_HIGH_PRECISION, default=True): cv.boolean,
        vol.Optional(CONF_USE_HEURISTICS, default=False): cv.boolean,
        vol.Optional(CONF_USE_SCHEDULES, default=False): cv.boolean,

        vol.Optional(CONF_LOCATION_IDX, default=0): cv.positive_int,
        vol.Optional(CONF_AWAY_TEMP, default=10): cv.positive_int,
        vol.Optional(CONF_OFF_TEMP, default=5): cv.positive_int,
    }),
}, extra=vol.ALLOW_EXTRA)


# these are for the controller's opmode/state and the zone's state
EVO_RESET      = 'AutoWithReset'
EVO_AUTO       = 'Auto'
EVO_AUTOECO    = 'AutoWithEco'
EVO_AWAY       = 'Away'
EVO_DAYOFF     = 'DayOff'
EVO_CUSTOM     = 'Custom'
EVO_HEATOFF    = 'HeatingOff'
# these are for zones' opmode, and state
EVO_FOLLOW     = 'FollowSchedule'
EVO_TEMPOVER   = 'TemporaryOverride'
EVO_PERMOVER   = 'PermanentOverride'
EVO_OPENWINDOW = 'OpenWindow'
EVO_FROSTMODE  = 'FrostProtect'

# bit masks for packets
EVO_MASTER = 0x01
EVO_TCS    = 0x03
EVO_SLAVE  = 0x04
EVO_ZONE   = 0x12
EVO_DHW    = 0x14

TCS_MODES = [EVO_RESET, EVO_AUTO, EVO_AUTOECO, EVO_AWAY, EVO_DAYOFF, EVO_CUSTOM, EVO_HEATOFF]
DHW_STATES = {STATE_ON : 'On', STATE_OFF : 'Off'}

SCAN_INTERVAL = 'scan_interval'
REFRESH_INTERVAL = 'refresh_interval'


def setup(hass, config):
    """Set up a Honeywell evoTouch heating system (1 controller and multiple zones).""" # noqa
    _LOGGER.debug("setup(), temperature units are: %s...", TEMP_CELSIUS)

### pull the configuration parameters  (TBD: excludes US-based systems)...
    hass.data[DATA_EVOHOME] = {}  # without this, KeyError: 'data_evohome'
    hass.data[DATA_EVOHOME]['timers'] = {}
    hass.data[DATA_EVOHOME]['config'] = dict(config[DOMAIN])

# scan_interval - when to update state (rounded up to nearest 60 ses)
    hass.data[DATA_EVOHOME]['config'][SCAN_INTERVAL] \
        = (int((config[DOMAIN][CONF_SCAN_INTERVAL] - 1) / 60) + 1) * 60
# refresh_interval - when to update installation / get new oauth session
    hass.data[DATA_EVOHOME]['config'][REFRESH_INTERVAL] \
        = OAUTH_TIMEOUT_SECS - hass.data[DATA_EVOHOME]['config'][SCAN_INTERVAL]

    if _LOGGER.isEnabledFor(logging.DEBUG):
        _tmp = dict(hass.data[DATA_EVOHOME]['config'])
        del _tmp[CONF_USERNAME]
        del _tmp[CONF_PASSWORD]

        _LOGGER.debug("Config data: %s", _tmp)
        _tmp = None

# no force_refresh - when instantiating client, it call client.installation()
### if called (for first time) from setup(), then there's no client yet...
    _LOGGER.debug("Connecting to the client (Honeywell web) API...")

    try:  ## client._login() is called by client.__init__()
### Use the evohomeclient2 API (which uses OAuth)
        from evohomeclient2 import EvohomeClient as EvohomeClient

        _LOGGER.debug("Calling v2 API [4 request(s)]: client.__init__()...")
        client = EvohomeClient(
            hass.data[DATA_EVOHOME]['config'][CONF_USERNAME],
            hass.data[DATA_EVOHOME]['config'][CONF_PASSWORD],
            debug=False
        )

    except:
        _LOGGER.error("Connect to client (Honeywell web) API: failed!")
        raise

    finally:
        del hass.data[DATA_EVOHOME]['config'][CONF_USERNAME]
        del hass.data[DATA_EVOHOME]['config'][CONF_PASSWORD]

# The latest evohomeclient uses: requests.exceptions.HTTPError, including:
# - 400 Client Error: Bad Request for url:      [ e.g. Bad credentials ]
# - 429 Client Error: Too Many Requests for url [ Limit exceeded ]
# - 503 Client Error:

    hass.data[DATA_EVOHOME]['evohomeClient'] = client

    _LOGGER.debug("Connect to client (Honeywell web) API: success")

    timeout = datetime.now()  # just done I/O
    hass.data[DATA_EVOHOME]['timers']['installExpires'] = timeout \
        + timedelta(seconds = hass.data[DATA_EVOHOME]['config'][REFRESH_INTERVAL])

    _LOGGER.debug("setup() Installation data expires: %s", timeout)

    _updateInstallData(hass.data[DATA_EVOHOME], force_refresh=False)

    _LOGGER.debug(
        "Location/TCS (temperature control system) used is: %s [%s]",
        hass.data[DATA_EVOHOME]['install'] \
            ['gateways'][0]['temperatureControlSystems'][0]['systemId'],
        hass.data[DATA_EVOHOME]['install'] \
            ['locationInfo']['name']
    )

    def _first_update(event):
        """???"""
        _LOGGER.debug("_first_update()")

## Finally, send a message to the master to di its first update
        _packet = {
            'sender': 'setup', 
            'signal': 'update',
            'to': EVO_MASTER
            }
        _LOGGER.debug(" - sending a dispatcher packet, %s...", _packet)
## this invokes def async_dispatcher_send(hass, signal, *args) on zones:
        hass.helpers.dispatcher.async_dispatcher_send(
            DISPATCHER_EVOHOME,
            _packet
        )

            
# create a listener for update packets...
    hass.bus.listen(EVENT_HOMEASSISTANT_START, _first_update)

# Load platforms...
    load_platform(hass, 'climate', DOMAIN)

    _LOGGER.debug("Finished: setup()")
    return True


def _updateStateData(domain_data, force_refresh=False):
    _LOGGER.debug("_updateStateData() begins...")

    _updateInstallData(domain_data, force_refresh)
#   _updateScheduleData(domain_data, force_refresh)
    _updateStatusData(domain_data, force_refresh)
    return


def _updateInstallData(domain_data, force_refresh=False):
    _LOGGER.debug("_updateInstallData() begins...")

    client = domain_data['evohomeClient']
    idx = domain_data['config'][CONF_LOCATION_IDX]


# is it time to fully refresh?
    if datetime.now() + timedelta(seconds=59) \
        > domain_data['timers']['installExpires']:
        force_refresh = True

# otherwise, were we asked to fully refresh...
    if force_refresh is True:

        try:  # re-authenticate
            client.locations = []  # remove stale data

            _LOGGER.debug("Calling v2 API [? request(s)]: client._login...")
            client._login()  # this invokes client.installation()

        except:
            _LOGGER.error("Refresh of client (Honeywell web API): failed!")
            raise

        _LOGGER.debug("Refresh of client (Honeywell web API): success")

        timeout = datetime.now()  # just done I/O
        domain_data['timers']['installExpires'] = timeout \
            + timedelta(seconds = domain_data['config'][REFRESH_INTERVAL])

        _LOGGER.debug("Installation data now expires after: %s", timeout)


## 0. As a precaution, REDACT the data we don't need
    if client.installation_info[0]['locationInfo']['locationId'] != 'REDACTED':
        for loc in client.installation_info:
            loc['locationInfo']['locationId'] = 'REDACTED'
            loc['locationInfo']['streetAddress'] = 'REDACTED'
            loc['locationInfo']['city'] = 'REDACTED'
            loc['locationInfo']['locationOwner'] = 'REDACTED'
            loc['gateways'][0]['gatewayInfo'] = 'REDACTED'


## 1. Obtain basic configuration (usu. 1/cycle)
    domain_data['install'] = client.installation_info[idx]


# Some of this data may need be redaction before getting into the logs
    if _LOGGER.isEnabledFor(logging.INFO):
        _tmp = dict(domain_data['install'])
        _tmp['locationInfo']['postcode'] = 'REDACTED'

        _LOGGER.debug("DATA_EVOHOME['install']: %s",
            _tmp)
        _LOGGER.debug("DATA_EVOHOME['timers']['installExpires']: %s",
            domain_data['timers']['installExpires'])
        _tmp = None

    return True


def _updateScheduleData(domain_data, force_refresh=False):
    _LOGGER.debug("_updateScheduleData() begins...")

    client = domain_data['evohomeClient']
    idx = domain_data['config'][CONF_LOCATION_IDX]


## 2. Optionally, obtain schedule (usu. 1/cycle): is emphemeral, so stored here
#   tcs = client.locations[idx]._gateways[0]._control_systems[0]
#   tcs = self._obj

#   if domain_data['config'][CONF_USE_SCHEDULES]:
#       domain_data['schedule'] \
#           = _returnZoneSchedules(self._obj)
#       domain_data['timers']['scheduleRefreshed'] \
#           = datetime.now()  # just done I/O


# Some of this data may need be redaction before getting into the logs
    if _LOGGER.isEnabledFor(logging.INFO):
        _LOGGER.debug("DATA_EVOHOME['schedule']: %s",
            domain_data['schedule'])
        _LOGGER.debug("DATA_EVOHOME]'timers']['scheduleRefreshed']: %s",
            domain_data['timers']['scheduleRefreshed'])

    return True


def _updateStatusData(domain_data, force_refresh=False):
    _LOGGER.debug("_updateStatusData() begins...")

    client = domain_data['evohomeClient']
    idx = domain_data['config'][CONF_LOCATION_IDX]

## 3. Obtain state (e.g. temps) (1/scan_interval)...
    if True:
        _LOGGER.debug("Calling v2 API [1 request(s)]: client.locations[idx].status()...")

    # this data is emphemeral, so store it
        ec2_status = client.locations[idx].status()
        ec2_tcs = ec2_status['gateways'][0]['temperatureControlSystems'][0]

        _LOGGER.debug("ec2_api.status() = %s", ec2_status)

    if domain_data['config'][CONF_HIGH_PRECISION] is True \
        and len(client.locations) > 1:
        _LOGGER.warn("Unable to increase precision of temperatures via the v1 api as there is more than one Location/TCS.  Continuing with v2 temps.")

    elif domain_data['config'][CONF_HIGH_PRECISION] is True:
        _LOGGER.debug("Trying to increase precision of temperatures via the v1 api...")
        try:
            from evohomeclient import EvohomeClient as EvohomeClientVer1
            ec1_api = EvohomeClientVer1(client.username, client.password)

            _LOGGER.debug("Calling v1 API [2 requests]: client.temperatures()...")
            ec1_temps = ec1_api.temperatures(force_refresh=True)  # a generator
            _LOGGER.debug("ev_api.temperatures() = %s", ec1_temps)

            for temp in ec1_temps:
                _LOGGER.debug("v1 Zone %s reports temp %s",
                    str(temp['id']) + " [" + temp['name'] + "]",
                    temp['temp']
                )

                for zone in ec2_tcs['zones']:
                    _LOGGER.debug(" - is it v2 Zone %s?",
                        zone['zoneId'] + " [" + zone['name'] + "]"
                    )

                    if str(temp['id']) == str(zone['zoneId']):
                        _LOGGER.debug(" - matched, old temp was %s, new is %s",
                            zone['temperatureStatus']['temperature'] \
                                if zone['temperatureStatus']['isAvailable'] \
                                else "isAvailable: False",
                            temp['temp']
                        )
                        if zone['temperatureStatus']['isAvailable']:
                            zone['temperatureStatus']['temperature'] \
                                = temp['temp']

                        break

        except:
            _LOGGER.warn("Failed to obtain higher-precision temperatures via the v1 api.  Continuing with v2 temps.")
            raise

        finally:
#           ec1_api = None  # do I need to clean this up?
            pass


    domain_data['status'] = ec2_tcs

    timeout = datetime.now()  # just done I/O
    domain_data['timers']['statusExpires'] = timeout \
            + timedelta(seconds = domain_data['config'][SCAN_INTERVAL])


# Some of this data may need be redaction before getting into the logs
    if _LOGGER.isEnabledFor(logging.INFO):
        _LOGGER.debug("DATA_EVOHOME['status']: %s",
            domain_data['status'])
        _LOGGER.debug("DATA_EVOHOME['timers']['statusExpires']: %s",
            domain_data['timers']['statusExpires'])

    return True


def UNUSED_SimulateDhw():
### ZX Hack for testing, DHW config...
    if False:
        _conf['gateways'][0]['temperatureControlSystems'][0]['dhw'] = \
            { \
                "dhwId": "999999", \
                "dhwStateCapabilitiesResponse": { \
                    "allowedStates": [ "On", "Off" ], \
                    "maxDuration": "1.00:00:00", \
                    "timingResolution": "00:10:00", \
                    "allowedModes": [ \
                        "FollowSchedule", \
                        "PermanentOverride", \
                        "TemporaryOverride" ] }, \
                "scheduleCapabilitiesResponse": { \
                    "minSwitchpointsPerDay": 1, \
                    "maxSwitchpointsPerDay": 6, \
                    "timingResolution": "00:10:00" } }
#       _LOGGER.debug("ZX _returnConfiguration() = %s", _conf)
### ZX Hack ends.

### ZX Hack for testing, DHW state...
    if False:
        ec2_tcs['dhw'] = \
            { \
                "dhwId": "999999", \
                "stateStatus": { \
                    "state": "On", \
                    "mode": "FollowSchedule" }, \
                "temperatureStatus": { \
                    "temperature": 61, \
                    "isAvailable": True }, \
                "activeFaults": [] }
#       _LOGGER.debug("ZX _returnTempsAndModes() = %s", ec2_tcs)
### ZX Hack ends.

    return



class evoEntity(Entity):
    """Base for Honeywell evohome slave devices (Heating/DHW zones)."""

    def __init__(self, hass, client, objRef):
        """Initialize the evohome Controller."""
        self.hass = hass
        self.client = client

        self._obj = objRef
        self._config = hass.data[DATA_EVOHOME]['config']

# create a listener for (internal) update packets...
        hass.helpers.dispatcher.async_dispatcher_connect(
            DISPATCHER_EVOHOME,
            self._connect
        )  # for: def async_dispatcher_connect(signal, target)

        return None  # __init__() should return None
    
    
    @property
    def available(self):
        """Dunno"""
        _LOGGER.debug("available(%s) = %s", self._id, self._available)
        return self._available

    @callback
    def _connect(self, packet):
        """Process a dispatcher connect."""
        _LOGGER.debug(
            "%s (type: %s) has received a '%s' packet from %s, to %s",
            self._id + " [" + self.name + "]",
            self._type,
            packet['signal'],
            packet['sender'],
            packet['to']
        )

        if packet['to'] & self._type:
            if packet['signal'] == 'update':
                _LOGGER.debug("%s is Calling: " \
                        + "schedule_update_ha_state(force_refresh=True)...",
                    self._id + " [" + self.name + "]"
                    )
# for all entity types this must have force_refresh=True 
                self.async_schedule_update_ha_state(force_refresh=True)
                self._assumed_state = False
                self._available = True

            elif packet['signal'] == 'assume':
                self._assumed_state = True

        return

        
    def _getZoneSchedTemp(self, zone, dt=None):

        _LOGGER.debug(
            "_getZoneSchedTemp(), schedule = %s",
            hass.data[DATA_EVOHOME]['schedule']
        )
        if dt is None: dt = datetime.now()
        _dayOfWeek = int(dt.strftime('%w'))  ## 0 is Sunday
        _timeOfDay = dt.strftime('%H:%M:%S')

#       _zone = self._obj.zones_by_id[zone['zoneId']]
        _zone = self._schedule[zone.zoneId]
        _LOGGER.debug("ZY Schedule: %s...", _zone._schedule)

        if _zone._schedule['refreshed'] > datetime.now():  # TBA 'expires'
            _LOGGER.debug(
                "Calling v2 API [1 request(s)]: zone.schedule(Zone=%s)...",
                self._id
            )
            _zone._schedule = self._obj.schedule()
            _zone._schedule['refreshed'] = datetime.now()


        # start with the last setpoint of yesterday, then
        for _day in _zone._schedule['DailySchedules']:
            if _day['DayOfWeek'] == (_dayOfWeek + 6) % 7:
                for _switchPoint in _day['Switchpoints']:
                    if _zone.zone_type == 'domesticHotWater':
                        _setPoint = _switchPoint['DhwState']
                    else:
                        _setPoint = _switchPoint['heatSetpoint']

        # walk through all of todays setpoints...
        for _day in _zone._schedule['DailySchedules']:
            if _day['DayOfWeek'] == _dayOfWeek:
                for _switchPoint in _day['Switchpoints']:
                    if _timeOfDay < _switchPoint['TimeOfDay']:
                        if _zone.zone_type == 'domesticHotWater':
                            _setPoint = _switchPoint['DhwState']
                        else:
                            _setPoint = _switchPoint['heatSetpoint']
                    else:
                        break

            return _setPoint


    def _getZoneById(self, zoneId, dataSource='status'):

        if dataSource == 'schedule':
            _zones = self.hass.data[DATA_EVOHOME]['schedule']

            if zoneId in _zones:
                return _zones[zoneId]
            else:
                raise KeyError("zone '", zoneId, "' not in dataSource")

        if dataSource == 'config':
            _zones = self.hass.data[DATA_EVOHOME]['installation'] \
                ['gateways'][0]['temperatureControlSystems'][0]['zones']

        else:  ## if dataSource == 'status':
            _zones = self.hass.data[DATA_EVOHOME]['status']['zones']

        for _zone in _zones:
            if _zone['zoneId'] == zoneId:
                return _zone
    # or should this be an IndexError?

        raise KeyError("Zone not found in dataSource, ID: ", zoneId)



class evoController(evoEntity):
    """Base for a Honeywell evohome TCS (temperature control system) hub device (aka Controller)."""

    def __init__(self, hass, client, objRef, objZones=[]):
        """Initialize the evohome Controller."""
        super().__init__(hass, client, objRef)

        self._id = objRef.systemId
        self._type = EVO_MASTER & EVO_TCS
        self._should_poll = True
        self._available = False

        self._zones = objZones
#       self._dhw = objDhw

#       self._config = hass.data[DATA_EVOHOME]['config']
        self._timers = hass.data[DATA_EVOHOME]['timers']

        self._install = hass.data[DATA_EVOHOME]['install']
#       self._timers['installExpires'] = datetime.now()

# create these here, but they're maintained in update()
        self._status = {}
        self._timers['statusExpires'] = datetime.now()

        if self._config[CONF_USE_SCHEDULES]:
            self._schedule = {}
            self._timers['scheduleExpires'] = datetime.now()

        _LOGGER.debug("__init__(TCS=%s), self._config = %s",
            self._id, self._config)
        _LOGGER.debug("__init__(TCS=%s), self._timers = %s",
            self._id, self._timers)
        _LOGGER.debug("__init__(TCS=%s), self._install = %s",
            self._id, self._install)

            
        return None  # __init__() should return None
    
    @property
    def should_poll(self):
        """Controller should TBA. The controller will provide the state data."""
        _LOGGER.debug("should_poll(TCS=%s) = %s", self._id, self._should_poll)
        return self._should_poll

    @property
    def force_update(self):
        """Controllers should update when state date is updated, even if it is unchanged."""
        _force = False
        _LOGGER.debug("force_update(TCS=%s) = %s", self._id,  _force)
        return _force

    @property
    def name(self):
        """Get the name of the controller."""
        _name = "_" + self._install['locationInfo']['name']
        _LOGGER.debug("name(TCS=%s) = %s", self._id, _name)
        return _name

    @property
    def icon(self):
        """Return the icon to use in the frontend UI."""
        _icon = "mdi:thermostat"
        _LOGGER.debug("icon(TCS=%s) = %s", self._id, _icon)
        return _icon

    @property
    def state(self):
        """Return the controller's current state (usually, its operation mode). After calling AutoWithReset, the controller  will enter Auto mode."""

        _opmode = self._status['systemModeStatus']['mode']

        if _opmode == EVO_RESET:
            _LOGGER.debug("state(TCS=%s) = %s (from %s)", self._id, EVO_AUTO, _opmode)
            return EVO_AUTO
        else:
            _LOGGER.debug("state(TCS=%s) = %s", self._id, _opmode)
            return _opmode

    @property
    def state_attributes(self):
        """Return the optional state attributes."""
        _data = {}

        if self.supported_features & SUPPORT_OPERATION_MODE:
            _data[ATTR_OPERATION_MODE] = self.current_operation
#           _data[ATTR_OPERATION_MODE] = self.hass.data[DATA_EVOHOME] \
#               ['status']['systemModeStatus']['mode']

            _data[ATTR_OPERATION_LIST] = self.operation_list
#           _oplist = []
#           for mode in self._install['gateways'][0] \
#               ['temperatureControlSystems'][0]['allowedSystemModes']:
#               _oplist.append(mode['systemMode'])
#           _data[ATTR_OPERATION_LIST] = _oplist

        _LOGGER.debug("state_attributes(TCS=%s) = %s",  self._id, _data)
        return _data

    @property
    def current_operation(self):
        """Return the operation mode of the controller."""

        _opmode = self._status['systemModeStatus']['mode']

        _LOGGER.debug("current_operation(TCS=%s) = %s", self._id, _opmode)
        return _opmode

    @property
    def operation_list(self):
        """Return the list of available operation modes."""
        _oplist = []
        for mode in self._install['gateways'][0] \
            ['temperatureControlSystems'][0]['allowedSystemModes']:
            _oplist.append(mode['systemMode'])

        _LOGGER.debug("operation_list(TCS=%s) = %s", self._id, _oplist)
        return _oplist


    def async_set_operation_mode(self, operation_mode):
        """Set new target operation mode. This method must be run in the event loop and returns a coroutine."""
        return self.hass.async_add_job(self.set_operation_mode, operation_mode)


    def set_operation_mode(self, operation_mode):
#   def set_operation_mode(self: ClimateDevice, operation: str) -> None:
        """Set new target operation mode for the TCS.

        'AutoWithReset may not be a mode in itself: instead, it _should_(?) lead to 'Auto' mode after resetting all the zones to 'FollowSchedule'. How should this be done?

        'Away' mode applies to the controller, not it's (slave) zones.

        'HeatingOff' doesn't turn off heating, instead: it simply sets setpoints to a minimum value (i.e. FrostProtect mode)."""

## At the start, the first thing to do is stop polled updates() until after
# set_operation_mode() has been called/effected
#       self.hass.data[DATA_EVOHOME]['lastUpdated'] = datetime.now()
        self._should_poll = False

        _LOGGER.debug(
            "set_operation_mode(TCS=%s, operation_mode=%s), current mode = %s",
            self._id,
            operation_mode,
            self._status['systemModeStatus']['mode']
        )

# PART 1: Call the api
        if operation_mode in TCS_MODES:
            _LOGGER.debug(
                "Calling v2 API [1 request(s)]: controller._set_status(%s)...",
                operation_mode
            )
## These 4 lines obligate only 1 location/controller, the 4th works for 1+
#           self.client._get_single_heating_system()._set_status(EVO_AUTO)
#           self.client.locations[0]._gateways[0]._control_systems[0]._set_status(EVO_AUTO)
#           self.client.set_status_normal
#           self._obj._set_status(EVO_AUTO)
            self._obj._set_status(operation_mode)

        else:
            raise NotImplementedError()


# PART 2: HEURISTICS - update the internal state of the Controller
## First, Update the state of the Controller
        if self._config[CONF_USE_HEURISTICS]:
            _LOGGER.debug(" - updating Controller state data")
## Do one of the following (sleep just doesn't work, convergence is too long)...
            self._status['systemModeStatus']['mode'] = operation_mode


# PART 3: HEURISTICS - update the internal state of the Zones
## For (slave) Zones, when the (master) Controller enters:
# EVO_AUTOECO, it resets EVO_TEMPOVER (but not EVO_PERMOVER) to EVO_FOLLOW
# EVO_DAYOFF,  it resets EVO_TEMPOVER (but not EVO_PERMOVER) to EVO_FOLLOW

        if self._config[CONF_USE_HEURISTICS]:
            _LOGGER.debug(
                " - updating Zone state data, Controller is '%s'",
                operation_mode
            )

## First, Inform the Zones that their state is now 'assumed'
            _packet = {
                'sender': 'controller', 
                'signal': 'assume',
                'to': EVO_SLAVE
                }
            _LOGGER.debug(" - sending a dispatcher packet, %s...", _packet)
## invokes def async_dispatcher_send(hass, signal, *args) on zones:
            self.hass.helpers.dispatcher.async_dispatcher_send(
                DISPATCHER_EVOHOME,
                _packet
            )

## Second, Update target_temp of the Zones
            _zones = self._status['zones']

            if operation_mode == EVO_CUSTOM:
                # target temps currently unknowable, await  next update()
                pass

            elif operation_mode == EVO_RESET:
                for _zone in _zones:
                    _zone[SETPOINT_STATUS]['setpointMode'] \
                        = EVO_FOLLOW
                # set target temps according to schedule?
                    if _zone[SETPOINT_STATUS]['setpointMode'] == EVO_FOLLOW \
                        and self._config[CONF_USE_SCHEDULES]:
                        _zone[SETPOINT_STATUS][TARGET_TEMPERATURE] \
                            = self._getZoneSchedTemp(_zone)

            elif operation_mode == EVO_AUTO:
                for _zone in _zones:
                    if _zone[SETPOINT_STATUS]['setpointMode'] != EVO_PERMOVER:
                        _zone[SETPOINT_STATUS]['setpointMode'] \
                            = EVO_FOLLOW
                # set target temps according to schedule?
                    if _zone[SETPOINT_STATUS]['setpointMode'] == EVO_FOLLOW \
                        and self._config[CONF_USE_SCHEDULES]:
                        _zone[SETPOINT_STATUS][TARGET_TEMPERATURE] \
                            = self._getZoneSchedTemp(_zone)

            elif operation_mode == EVO_AUTOECO:
                for _zone in _zones:
                    if _zone[SETPOINT_STATUS]['setpointMode'] != EVO_PERMOVER:
                        _zone[SETPOINT_STATUS]['setpointMode'] \
                            = EVO_FOLLOW
                # set target temps according to schedule?, but less 3
                    if _zone[SETPOINT_STATUS]['setpointMode'] == EVO_FOLLOW \
                        and self._config[CONF_USE_SCHEDULES]:
                        _zone[SETPOINT_STATUS][TARGET_TEMPERATURE] \
                            = self._getZoneSchedTemp(_zone) - 3

            elif operation_mode == EVO_DAYOFF:
                for _zone in _zones:
                    if _zone[SETPOINT_STATUS]['setpointMode'] != EVO_PERMOVER:
                        _zone[SETPOINT_STATUS]['setpointMode'] \
                            = EVO_FOLLOW
                # set target temp according to schedule?, but for Saturday
                    if _zone[SETPOINT_STATUS]['setpointMode'] == EVO_FOLLOW \
                        and self._config[CONF_USE_SCHEDULES]:
                        _dt = datetime.now()
                        _dt += timedelta(days = 6 - int(_dt.strftime('%w')))
                        _zone[SETPOINT_STATUS][TARGET_TEMPERATURE] \
                            = self._getZoneSchedTemp(_zone, dt)

            elif operation_mode == EVO_AWAY:
                for _zone in _zones:
                    if _zone[SETPOINT_STATUS]['setpointMode'] != EVO_PERMOVER:
                        _zone[SETPOINT_STATUS]['setpointMode'] = EVO_FOLLOW
# Leave this for slave.current_temperature
#               # default target for 'Away' is 10C, assume that for now
#                   if self._config[CONF_USE_SCHEDULES]:
#                       _zone[SETPOINT_STATUS][TARGET_TEMPERATURE] \
#                           = self._config[CONF_AWAY_TEMP]
                if 'dhw' in self._status:
                    _zone = self._status['dhw']
                    if _zone['stateStatus']['mode'] != EVO_PERMOVER:
                        _zone['stateStatus']['mode'] = EVO_FOLLOW
                        _zone['stateStatus']['state'] = STATE_OFF

            elif operation_mode == EVO_HEATOFF:
                for _zone in _zones:
                    if _zone[SETPOINT_STATUS]['setpointMode'] != EVO_PERMOVER:
                        _zone[SETPOINT_STATUS]['setpointMode'] \
                            = EVO_FOLLOW
# Leave this for slave.current_temperature
#               # default target for 'HeatingOff' is 5C, assume that for now
#                   if self._config[CONF_USE_SCHEDULES]:
#                       _zone[SETPOINT_STATUS][TARGET_TEMPERATURE] = 5


## Finally, , Inform the Zones that their state may have changed
#           self.hass.bus.fire('mode_changed', {ATTR_ENTITY_ID: self._scs_id, ATTR_STATE: command})
            _packet = {
                'sender': 'controller', 
                'signal': 'update',
                'to': EVO_SLAVE
                }
            _LOGGER.debug(" - sending a dispatcher packet, %s...", _packet)
## invokes def async_dispatcher_send(hass, signal, *args) on zones:
            self.hass.helpers.dispatcher.async_dispatcher_send(
                DISPATCHER_EVOHOME,
                _packet
            )

## At the end, the last thing to do is restart updates()
        self.hass.data[DATA_EVOHOME]['lastUpdated'] = datetime.now()
        self._should_poll = True

        return None

    @property
    def is_away_mode_on(self):
        """Return true if away mode is on."""
        _away = self._status['systemModeStatus']['mode'] == EVO_AWAY
        _LOGGER.debug("is_away_mode_on(TCS=%s) = %s", self._id, _away)
        return _away


    def async_turn_away_mode_on(self):
        """Turn away mode on.

        This method must be run in the event loop and returns a coroutine.
        """
        return self.hass.async_add_job(self.turn_away_mode_on)


    def turn_away_mode_on(self):
        """Turn away mode on."""
        _LOGGER.debug("turn_away_mode_on(TCS=%s)", self._id)
        self.set_operation_mode(EVO_AWAY)
        return


    def async_turn_away_mode_off(self):
        """Turn away mode off.

        This method must be run in the event loop and returns a coroutine.
        """
        return self.hass.async_add_job(self.turn_away_mode_off)


    def turn_away_mode_off(self):
        """Turn away mode off."""
        _LOGGER.debug("turn_away_mode_off(TCS=%s)", self._id)
        self.set_operation_mode(EVO_AUTO)
        return

    @property
    def supported_features(self):
        """Get the list of supported features of the controller."""
# It will likely be the case we need to support Away/Eco/Off modes in the HA
# fashion, even though these modes are subtly different - this will allow tight
# integration with the HA landscape e.g. Alexa/Google integration
        _flags = SUPPORT_OPERATION_MODE | SUPPORT_AWAY_MODE
        _LOGGER.debug("supported_features(TCS=%s) = %s", self._id, _flags)
        return _flags


    def update(self):
# We can't use async_update() because the client api is not asyncio
        """Get the latest state (operating mode) of the controller and
        update the state (temp, setpoint) of all children zones.

        Get the latest schedule of the controller every hour."""

        _LOGGER.debug("update(TCS=%s)", self._id)
# Wait a minimum of scan_interval/60 minutes(rounded down) between updates
        _timeout = datetime.now() + timedelta(seconds=59)

# Exit now if timer has not expired
        _expired = _timeout > self._timers['statusExpires']

        _LOGGER.debug("update(TCS) time = %s %s statusExpires = %s",
            _timeout, ">" if _expired else "<",
            self._timers['statusExpires'])

        if not _expired:  # timer not expired, so exit
            _LOGGER.debug(
                "update(TCS) scan_interval not expired, skipping update"
            )
            return

        _LOGGER.debug("update(TCS), self.hass.data[DATA_EVOHOME] (before) = %s", self.hass.data[DATA_EVOHOME])

## Otherwise do a simple update, or a full refresh
        _expired = _timeout > self._timers['installExpires']

        _LOGGER.debug("update(TCS) time = %s %s installExpires = %s",
            _timeout, ">" if _expired else "<",
            self._timers['installExpires'])

        if _expired:  # do a full_refresh, installation & status
            _LOGGER.debug("update(TCS) oauth Token expired: full refresh...",)
            _updateStateData(self.hass.data[DATA_EVOHOME], force_refresh=True)

        else:  # do a simple update of status (state data)
            _LOGGER.debug("update(TCS) oauth Token unexpired: update only...")
            _updateStateData(self.hass.data[DATA_EVOHOME])

        self._status = self.hass.data[DATA_EVOHOME]['status']

        _LOGGER.debug("update(TCS), self.hass.data[DATA_EVOHOME] (after) = %s", self.hass.data[DATA_EVOHOME])

## Finally, send a message to the slaves to update themselves
        _packet = {
            'sender': 'controller', 
            'signal': 'update',
            'to': EVO_SLAVE
            }
        _LOGGER.debug(" - sending a dispatcher packet, %s...", _packet)
## this invokes def async_dispatcher_send(hass, signal, *args) on zones:
        self.hass.helpers.dispatcher.async_dispatcher_send(
            DISPATCHER_EVOHOME,
            _packet
        )

        _LOGGER.debug("update(TCS=%s), self._install = %s",
            self._id, self._install)
        _LOGGER.debug("update(TCS=%s), self._status = %s",
            self._id, self._status)
        if self._config[CONF_USE_SCHEDULES]:
            _LOGGER.debug("update(TCS=%s), self._schedule = %s",
                self._id, self._schedule)

        return True



class evoSlaveEntity(evoEntity):
    """Base for Honeywell evohome slave devices (Heating/DHW zones)."""

    def __init__(self, hass, client, objRef):
        """Initialize the evohome evohome Heating/DHW zone."""
        super().__init__(hass, client, objRef)

        self._id = objRef.zoneId  # OK for DHW too, as == objRef.dhwId
        self._type = EVO_SLAVE

        self._assumed_state = True  # is this right for polled IOT devices?
        self._available = False

        self._config = hass.data[DATA_EVOHOME]['config']
#       self._timers = hass.data[DATA_EVOHOME]['timers']

        if self._obj.zone_type == 'domesticHotWater':
            self._install = hass.data[DATA_EVOHOME]['install'] \
                ['gateways'][0]['temperatureControlSystems'][0]['dhw']
        else:
            for _zone in hass.data[DATA_EVOHOME]['install'] \
                ['gateways'][0]['temperatureControlSystems'][0]['zones']:
                if _zone['zoneId'] == self._id:
                    self._install = _zone
                    break

# create these here, but they're maintained in update()
        self._status = {}
        self._schedule = {} # if self._config[CONF_USE_SCHEDULES]

        _LOGGER.debug("__init__(%s), self._config = %s",
            self._id, self._config)
#       _LOGGER.debug("__init__(%s), self._timers = %s",
#           self._id, self._timers)
        _LOGGER.debug("__init__(%s), self._install = %s",
            self._id, self._install)

        return None  # __init__() should return None

    @property
    def should_poll(self):
        """Slaves (heating/DHW zones) should not be polled as the (master) Controller maintains state data."""
        _poll = False
        _LOGGER.debug("should_poll(%s) = %s", self._id, _poll)
        return _poll

    @property
    def force_update(self):
        """Slaves (heating/DHW zones) are not (normally) polled, and should be forced to update."""
        _force = False
        _LOGGER.debug("force_update(%s) = %s", self._id, _force)
        return _force

    @property
    def supported_features(self):
        """Return the list of supported features of the Heating/DHW zone."""
        if self._obj.zone_type == 'domesticHotWater':
            _feats = SUPPORT_OPERATION_MODE | SUPPORT_ON_OFF
        else:
            _feats = SUPPORT_OPERATION_MODE | SUPPORT_TARGET_TEMPERATURE

        _LOGGER.debug("supported_features(%s) = %s", self._id, _feats)
        return _feats

    @property
    def operation_list(self):
        """Return the list of operating modes of the Heating/DHW zone."""
# this list is hard-coded so for a particular order
#       if self._obj.zone_type != 'domesticHotWater':
#           _oplist = self._install \
#               [SETPOINT_CAPABILITIES]['allowedSetpointModes']
        _oplist = (EVO_FOLLOW, EVO_TEMPOVER, EVO_PERMOVER) # trying...
#       _oplist = [EVO_FOLLOW, EVO_TEMPOVER, EVO_PERMOVER] # this works
        _LOGGER.debug("operation_list(%s) = %s", self._id, _oplist)
        return _oplist

    @property
    def current_operation(self):
        """Return the current operating mode of the Heating/DHW zone."""
        if self._obj.zone_type == 'domesticHotWater':
            _opmode = self._status['stateStatus']['mode']
        else:
            _opmode = self._status[SETPOINT_STATUS]['setpointMode']

        _LOGGER.debug("current_operation(%s) = %s", self._id, _opmode)
        return _opmode


    def async_set_operation_mode(self, operation_mode):
#   def async_set_operation_mode(self, operation_mode, setpoint=None, until=None):
        """Set new target operation mode.

        This method must be run in the event loop and returns a coroutine.
        """
# Explicitly added, cause I am not sure of impact of adding parameters to this
        _LOGGER.warn(
            "async_set_operation_mode(%s, operation_mode=%s)",
            self._id,
            operation_mode
            )
        return self.hass.async_add_job(self.set_operation_mode, operation_mode)

    @property
    def name(self):
        """Return the name to use in the frontend UI."""
        if self._obj.zone_type == 'domesticHotWater':
            _name = '~DHW'
        else:
            _name = self._obj.name

        _LOGGER.debug("name(%s) = %s", self._id, _name)
        return _name

    @property
    def icon(self):
        """Return the icon to use in the frontend UI."""
        if self._obj.zone_type == 'domesticHotWater':
            _icon = "mdi:thermometer"
        else:
            _icon = "mdi:radiator"

        _LOGGER.debug("icon(%s) = %s", self._id, _icon)
        return _icon

    @property
    def current_temperature(self):
        """Return the current temperature of the Heating/DHW zone."""
# TBD: use client's own state data (should work for DHW too)
#       _status = self._status

# TBD: since that doesn't work (yet), use hass.data[DATA_EVOHOME]['status']
        if self._obj.zone_type == 'domesticHotWater':
            _status = self.hass.data[DATA_EVOHOME]['status']['dhw']
        else:
            _status = self._getZoneById(self._id, 'status')

# ... then, in either case:
        if _status['temperatureStatus']['isAvailable']:
            _temp = _status['temperatureStatus']['temperature']
            _LOGGER.debug("current_temperature(%s) = %s", self._id, _temp)
        else:
            _temp = None
            _LOGGER.warn("current_temperature(%s) - is unavailable", self._id)

        _LOGGER.debug("current_temperature(%s) Method 1 = %s, Method 2 = %s",
            self._id, _status['temperatureStatus'],
            self._status['temperatureStatus'],
            )

        return _temp

    @property
    def min_temp(self):
        """Return the minimum setpoint (target temp) of the Heating zone.
        Setpoints are 5-35C by default, but zones can be further limited."""
# Only applies to Heating zones (SUPPORT_TARGET_TEMPERATURE), not DHW
        if self._obj.zone_type == 'domesticHotWater':
            _temp = None
        else:
            _temp = self._install[SETPOINT_CAPABILITIES]['minHeatSetpoint']

        _LOGGER.debug("min_temp(%s) = %s", self._id, _temp)
        return _temp

    @property
    def max_temp(self):
        """Return the maximum setpoint (target temp) of the Heating zone.
        Setpoints are 5-35C by default, but zones can be further limited."""
# Only applies to Heating zones (SUPPORT_TARGET_TEMPERATURE), not DHW
        if self._obj.zone_type == 'domesticHotWater':
            _temp = None
        else:
            _temp = self._install[SETPOINT_CAPABILITIES]['maxHeatSetpoint']

        _LOGGER.debug("max_temp(%s) = %s", self._id, _temp)
        return _temp

    @property
    def target_temperature_step(self):
        """Return the step of setpont (target temp) of the Heating zone."""
# Currently only applies to Heating zones (SUPPORT_TARGET_TEMPERATURE), not DHW
#       _step = self._config \
#           [SETPOINT_CAPABILITIES]['valueResolution']
        if self._obj.zone_type == 'domesticHotWater':
            _step = None
        else:
# is usually PRECISION_HALVES
            _step = PRECISION_HALVES

        _LOGGER.debug("target_temperature_step(%s) = %s", self._id,_step)
        return _step

    @property
    def temperature_unit(self):
        """Return the temperature unit to use in the frontend UI."""
        _LOGGER.debug("temperature_unit(%s) = %s", self._id, TEMP_CELSIUS)
        return TEMP_CELSIUS

    @property
    def precision(self):
        """Return the temperature precision to use in the frontend UI."""
        if self._obj.zone_type == 'domesticHotWater':
            _precision = PRECISION_WHOLE  # Honeywell has (e.g.) 62C, not 62.0C
        elif self._config[CONF_HIGH_PRECISION]:
            _precision = PRECISION_TENTHS
        else:
            _precision = PRECISION_HALVES

        _LOGGER.debug("precision(%s) = %s", self._id, _precision)
        return _precision

    @property
    def assumed_state(self) -> bool:
        """Return True if unable to access real state of the entity."""
# After (say) a controller.set_operation_mode, it will take a while for the
# 1. (invoked) client api call (request.xxx) to reach the web server,
# 2. web server to send message to the controller
# 3. controller to get message to zones
# 4. controller to send message to web server
# 5. next client api call (every scan_interval)
# in between 1. and 5., should assumed_state be True ??

        _LOGGER.debug("assumed_state(%s) = %s", self._id, self._assumed_state)
        return self._assumed_state


    def update(self):
        """Get the latest state data (e.g. temp.) of the Heating/DHW zone."""

        _LOGGER.debug("update(TCS=%s)", self._id)
# Needs IDX?
        if self._obj.zone_type == 'domesticHotWater':
            self._install = self.hass.data[DATA_EVOHOME]['install'] \
                ['gateways'][0]['temperatureControlSystems'][0]['dhw']
            self._status = self.hass.data[DATA_EVOHOME]['status']['dhw']
        else:
            for _zone in self.hass.data[DATA_EVOHOME]['install'] \
                ['gateways'][0]['temperatureControlSystems'][0]['zones']:
                if _zone['zoneId'] == self._id:
                    self._install = _zone
                    break
            for _zone in self.hass.data[DATA_EVOHOME]['status']['zones']:
                if _zone['zoneId'] == self._id:
                    self._status = _zone
                    break

# WIP - need to add an expiry timer...
        if self._config[CONF_USE_SCHEDULES]:
            _LOGGER.debug(
                "Calling v2 API [1 request(s)]: zone.schedule(Zone=%s)...",
                self._id
            )
#           self.schedule = {}
            self._schedule = self._obj.schedule()
            self._schedule['refreshed'] = datetime.now()

#           self.hass.data[DATA_EVOHOME]['schedule'] = {}  # done when TCS was init'd
            self.hass.data[DATA_EVOHOME]['schedule'][self._id] \
                = {'name'      : self.name,
                   'schedule'  : self._schedule,
                   'refreshed' : self._schedule['refreshed']}
        else:
            self._schedule = None


        _LOGGER.debug("update(%s), self._install = %s",
            self._id, self._install)
        _LOGGER.debug("update(%s), self._status = %s",
            self._id, self._status)
        if self._config[CONF_USE_SCHEDULES]:
            _LOGGER.debug("update(%s), self._schedule = %s",
                self._id, self._schedule)

        return True



class evoZone(evoSlaveEntity, ClimateDevice):
    """Base for a Honeywell evohome Heating zone (aka Zone)."""

    @property
    def _sched_temperature(self, datetime=None):
        """Return the temperature we try to reach."""
        _temp = self._schedule
# TBA
        _LOGGER.debug(
            "_sched_temperature(Zone=%s) = %s",
            self._id,
            _temp
        )

    @property
    def state(self):
        """Return the Zone's current state (usually, its operation mode).

        A zone's state is usually its operation mode, but they can enter
        OpenWindowMode autonomously, or they can be 'Off', or just set to 5.0C.
        In all three case, the client api seems to report 5C.
        
        This is complicated futher by the possibility that the minSetPoint is
        greater than 5C."""

        state = self._status[SETPOINT_STATUS]['setpointMode']

        zone_target = self._status[SETPOINT_STATUS][TARGET_TEMPERATURE]
        zone_opmode = self._status[SETPOINT_STATUS]['setpointMode']
        tcs_opmode = self.hass.data[DATA_EVOHOME]['status'] \
            ['systemModeStatus']['mode']
        
# Optionally, use heuristics to override reported state (mode)
        if self._config[CONF_USE_HEURISTICS]:
            if tcs_opmode == EVO_HEATOFF: 
                state = EVO_FROSTMODE
            elif zone_target == 5:
                if zone_opmode == EVO_FOLLOW:
#                   if sched_temp <> 5 --> state = EVO_OPENWINDOW
                    pass
            else:
                pass

            _LOGGER.debug("state(Zone=%s) = %s (using heuristics)", 
                self._id, 
                state
                )
        else:
            _LOGGER.debug("state(Zone=%s) = %s (latest actual)", 
                self._id, 
                state
                )
        
        _LOGGER.debug(
            "state(Zone=%s) = %s [tcs_opmode=%s, opmode=%s, target=%s]",
            self._id + " [" + self.name + "]",
            state,
            tcs_opmode,
            zone_opmode,
            zone_target
            )
        return state

    @property
    def state_attributes(self):
        """Return the optional state attributes."""
        data = {
            ATTR_CURRENT_TEMPERATURE: show_temp(
                self.hass, self.current_temperature, self.temperature_unit,
                self.precision),
            ATTR_MIN_TEMP: show_temp(
                self.hass, self.min_temp, self.temperature_unit,
                self.precision),
            ATTR_MAX_TEMP: show_temp(
                self.hass, self.max_temp, self.temperature_unit,
                self.precision),
            ATTR_TEMPERATURE: show_temp(
                self.hass, self.target_temperature, self.temperature_unit,
                self.precision),
        }

        supported_features = self.supported_features
        if self.target_temperature_step is not None:
            data[ATTR_TARGET_TEMP_STEP] = self.target_temperature_step

        if supported_features & SUPPORT_TARGET_TEMPERATURE_HIGH:
            data[ATTR_TARGET_TEMP_HIGH] = show_temp(
                self.hass, self.target_temperature_high, self.temperature_unit,
                self.precision)

        if supported_features & SUPPORT_TARGET_TEMPERATURE_LOW:
            data[ATTR_TARGET_TEMP_LOW] = show_temp(
                self.hass, self.target_temperature_low, self.temperature_unit,
                self.precision)

        if supported_features & SUPPORT_OPERATION_MODE:
            data[ATTR_OPERATION_MODE] = self.current_operation
            if self.operation_list:
                data[ATTR_OPERATION_LIST] = self.operation_list

        if supported_features & SUPPORT_AWAY_MODE:
            is_away = self.is_away_mode_on
            data[ATTR_AWAY_MODE] = STATE_ON if is_away else STATE_OFF

        _LOGGER.debug("state_attributes(Zone=%s) = %s", self._id, data)
        return data

    @property
    def target_temperature(self):
        """Return the temperature we try to reach."""

        _temp = self._status[SETPOINT_STATUS][TARGET_TEMPERATURE]

        _LOGGER.debug(
            "target_temperature(Zone=%s) = %s",
            self._id + " [" + self.name + "]",
            _temp
        )

        return _temp


    def set_operation_mode(self, operation_mode, temperature, until):
#   def set_operation_mode(self, operation_mode, **kwargs):
        """Set an operating mode for the Zone."""
        _LOGGER.debug("set_operation_mode(Zone=%s, OpMode=%s, Temp=%s, Until=%s)",
            self._id + " [" + self.name + "]",
            operation_mode,
            temperature,
            until
            )

#           temperature  = kwargs.get(ATTR_TEMPERATURE)
#           until = kwargs.get(ATTR_UNTIL)
            
# FollowSchedule - return to scheduled target temp (indefinitely)
        if operation_mode == EVO_FOLLOW:
            if temperature is not None or until is not None:
                _LOGGER.warn("set_operation_mode(%s): For '%s' mode, " \
                        + "'temperature' and 'until' should both be None " \
                        + "(will ignore them).",
                    self._id + " [" + self.name + "]",
                    operation_mode
                    )

            _LOGGER.debug("Calling API [? request(s)]: " \
                    + "zone.cancel_temp_override()..."
                )
            self._obj.cancel_temp_override(self._obj)

        else:
            if temperature is None:
                _LOGGER.warn("set_operation_mode(%s): For '%s' mode, " \
                        + "'temperature' should not be None " \
                        + "(will use current target temp).",
                    self._id + " [" + self.name + "]",
                    operation_mode
                    )
                temperature = self._status[SETPOINT_STATUS][TARGET_TEMPERATURE]

# PermanentOverride - override target temp indefinitely
        if operation_mode == EVO_PERMOVER:
            if until is not None:
                _LOGGER.warn("set_operation_mode(%s): For '%s' mode, "\
                        + "'until' should be None " + \
                        + "(will ignore it).",
                    self._id + " [" + self.name + "]",
                    operation_mode
                    )

            _LOGGER.debug("Calling API [? request(s)]: " \
                    + " zone.set_temperature(%s)...", 
                temperature 
                )
            self._obj.set_temperature(temperature)

# TemporaryOverride - override target temp, for a hour by default
        elif operation_mode == EVO_TEMPOVER:
            if until is None:
                _LOGGER.warn("set_operation_mode(%s): For '%s' mode, "\
                    + "'until' should not be None " \
                    + "(will use 1 hour from now).",
                    self._id + " [" + self.name + "]",
                    operation_mode
                    )
                # UTC_OFFSET_TIMEDELTA = datetime.now() - datetime.utcnow()
                until = datetime.now() + timedelta(1/24) ## use .utcnow() or .now() ??

            _LOGGER.debug("Calling API [? request(s)]: " \
                    + "zone.set_temperature(%s, %s)...", 
                temperature, 
                until
                )
            self._obj.set_temperature(temperature, until)

# Optionally, use heuristics to update state
        if self._config[CONF_USE_HEURISTICS]:
            _LOGGER.debug("set_operation_mode(): Action completed, " \
                    + "updating local state data using heuristics..."
                )

            self._status[SETPOINT_STATUS]['setpointMode'] \
                = operation_mode

            if operation_mode == EVO_FOLLOW:
                if self._config[CONF_USE_SCHEDULES]:
                    temperature = self._getZoneSchedTemp(
                        self._status['zoneId'], datetime.now())
                    self._status[SETPOINT_STATUS][TARGET_TEMPERATURE] \
                        = temperature
            else:
                self._status[SETPOINT_STATUS][TARGET_TEMPERATURE] = temperature

            _LOGGER.debug("set_operation_mode(): Calling " \
                    + "controller.schedule_update_ha_state()"
                )
            self.async_schedule_update_ha_state(force_refresh=False)

        return True


    def set_temperature(self, **kwargs):
        """Set a target temperature (setpoint) for the Zone."""
        _LOGGER.debug("set_temperature(Zone=%s, **kwargs)",
            self._id + " [" + self.name + "]"
            )

#       for name, value in kwargs.items():
#           _LOGGER.debug('%s = %s', name, value)

        temperature = kwargs.get(ATTR_TEMPERATURE)

        if temperature is None:
            _LOGGER.error("set_temperature(%s): Temperature must not be None." \
                    + "(cancelling call).",
                self._id + " [" + self.name + "]",
                temperature
                )
            return False

        max_temp = self.install[SETPOINT_CAPABILITIES]['maxHeatSetpoint']
        if temperature > max_temp:
            _LOGGER.error("set_temperature(%s): Temp %s is above maximum, %s!",
                self._id + " [" + self.name + "]",
                temperature,
                max_temp
                )
            return False

        min_temp = self.install[SETPOINT_CAPABILITIES]['minHeatSetpoint']
        if _temperature < _min_temp:
            _LOGGER.error("set_temperature(%s): Temp %s is below minimum, %s!",
                self._id + " [" + self.name + "]",
                _min_temp
                )
            return False

        until = kwargs.get(ATTR_UNTIL)
# If is None: PermanentOverride - override target temp indefinitely
#  otherwise: TemporaryOverride - override target temp, until some time

        _LOGGER.debug("Calling API [? request(s)]: " \
                + "zone.set_temperature(%s, %s)...", 
            setpoint, 
            until
            )
        self._obj.set_temperature(temperature, until)

# Optionally, use heuristics to update state
        if self._config[CONF_USE_HEURISTICS]:
            _LOGGER.debug("set_operation_mode(): Action completed, " \
                    + "updating local state data using heuristics..."
                )

            self._status[SETPOINT_STATUS]['setpointMode'] \
                = EVO_PERMOVER if until is None else EVO_TEMPOVER

            self._status[SETPOINT_STATUS][TARGET_TEMPERATURE] \
                = temperature

            _LOGGER.debug("set_temperature(): Calling " \
                    + "controller.schedule_update_ha_state()"
                )
            self.async_schedule_update_ha_state(force_refresh=False)

        return True



class evoDhwEntity(evoSlaveEntity):
    """Base for a Honeywell evohome DHW zone (aka DHW)."""

    @property
    def _get_state(self):
        """Return the reported state of the DHW..

        Is asyncio friendly."""

        _state = None

        if self._config[CONF_USE_HEURISTICS]:
            _cont_opmode = self.hass.data[DATA_EVOHOME]['status'] \
                ['systemModeStatus']['mode']

            if _cont_opmode == EVO_AWAY:
                _state = DHW_STATES[STATE_OFF]
                _LOGGER.debug("_get_state(DHW=%s), state is %s (using heuristics)", self._id, _state)

# if we haven't yet figured out the DHW's state as yet, then:
        if _state is None:
            _state = self._status['stateStatus']['state']

            if self.assumed_state:
                _LOGGER.debug("_get_state(DHW=%s), state is %s (assumed)", self._id, _state)
            else:
                _LOGGER.debug("_get_state(DHW=%s), state is %s (latest actual)", self._id, _state)

        _LOGGER.debug("_get_state(DHW=%s) = %s", self._id, _state)
        return _state


    def _set_state(self, _state, _mode=None, _until=None) -> None:
        """Turn DHW on/off for an hour, until next setpoint, or indefinitely."""

        if _state is None:
            _state = self.state

        if _mode is None:
            _mode = EVO_TEMPOVER

        if _mode != EVO_TEMPOVER:
            _until = None
        else:
            if _until is None:
                _until = datetime.now() + timedelta(hours=1)

            _until =_until.strftime('%Y-%m-%dT%H:%M:%SZ')

        _data =  {'State':_state, 'Mode':_mode, 'UntilTime':_until}

        _LOGGER.debug("Calling v2 API [1 request(s)]: dhw._set_dhw(%s)...", _data)
        self._obj._set_dhw(_data)

        self._status['stateStatus']['state'] = _state
        self._assumed_state = True
        self.async_schedule_update_ha_state(force_refresh=False)

        return None

    @property
    def name(self):
        """Return the name to use in the frontend UI."""
        if self.supported_features & SUPPORT_OPERATION_MODE:
            _name = "~DHW (sensor)"
        else:
            _name = "~DHW"

        _LOGGER.debug("name(DHW=%s) = %s", self._id, _name)
        return _name

    @property
    def icon(self):
        """Return the icon to use in the frontend UI."""
        if self.supported_features & SUPPORT_OPERATION_MODE:
            _icon = "mdi:thermometer-lines"
        else:
            _icon = "mdi:thermometer"

        _LOGGER.debug("icon(%s) = %s", self._id, _icon)
        return _icon

    @property
    def state(self) -> str:
        """Return the state."""
        _state = STATE_ON if self._get_state == DHW_STATES[STATE_ON] \
            else STATE_OFF

        _LOGGER.debug("state(DHW=%s) = %s",
            self._id + " [" + self.name + "]",
            _state
        )
        return _state


    def set_operation_mode(self, operation_mode):
        """Set new operation mode for the DHW controller."""
        _LOGGER.debug("set_operation_mode(Zone=%s, OpMode=%s, Until=%s)",
            self._id + " [" + self.name + "]",
            operation_mode,
            until
            )

# FollowSchedule - return to scheduled target temp (indefinitely)
        if operation_mode == EVO_FOLLOW:
            _state = ''
        else:
            _state = self.state

        _mode = operation_mode

# PermanentOverride - override target temp indefinitely
# TemporaryOverride - override target temp, for a hour by default
        if operation_mode == EVO_TEMPOVER:
            _until = datetime.now() + timedelta(hours=1)
            _until =_until.strftime('%Y-%m-%dT%H:%M:%SZ')
        
        else:
            _until = None

        self._set_state(_state, _mode, _until)

        _LOGGER.debug("set_operation_mode(DHWt=%s, %s, %s, %s)",
            self._id + " [" + self.name + "]",
            _state, _mode, _until
        )
        return



class evoDhwSensor(evoDhwEntity, ClimateDevice):
    """Base for a Honeywell evohome DHW zone (aka DHW)."""

    @property
    def supported_features(self):
        """Return the list of supported features of the Heating/DHW zone."""
        _feats = SUPPORT_OPERATION_MODE
        _LOGGER.debug("supported_features(DHWt=%s) = %s", self._id, _feats)
        return _feats

    @property
    def state_attributes(self):
        """Return the optional state attributes."""
# The issue with HA's state_attributes() is that is assumes Climate objects
# have a:
# - self.current_temperature:      True for Heating & DHW zones
# - self.target_temperature:       True for DHW zones only
# - self.min_temp & self.max_temp: True for DHW zones only

# so we have...
        data = {
            ATTR_CURRENT_TEMPERATURE: show_temp(
                self.hass, self.current_temperature, self.temperature_unit,
                self.precision),
# DHW does not have a min_temp, max_temp, or target temp
        }

        supported_features = self.supported_features

        if supported_features & SUPPORT_OPERATION_MODE:
            data[ATTR_OPERATION_MODE] = self.current_operation
            data[ATTR_OPERATION_LIST] = self.operation_list

#       if supported_features & SUPPORT_AWAY_MODE:
#           is_away = self.is_away_mode_on
#           data[ATTR_AWAY_MODE] = STATE_ON if is_away else STATE_OFF

        if supported_features & SUPPORT_ON_OFF:
            data = {}

        _LOGGER.debug(
            "state_attributes(DHWt=%s) = %s",
            self._id + " [" + self.name + "]",
            data
        )
        return data



class evoDhwSwitch(evoDhwEntity, ToggleEntity):
    """Base for a Honeywell evohome DHW zone (aka DHW)."""

    @property
    def supported_features(self):
        """Return the list of supported features of the Heating/DHW zone."""
        _feats = SUPPORT_ON_OFF
        _LOGGER.debug("supported_features(DHWs%s) = %s", self._id, _feats)
        return _feats

    @property
    def state_attributes(self):
        """Return the optional state attributes."""

        data = { }

        supported_features = self.supported_features

        if supported_features & SUPPORT_ON_OFF:
            pass

        _LOGGER.debug(
            "state_attributes(DHWs%s) = %s",
            self._id + " [" + self.name + "]",
            data
        )
        return data

    @property
    def OUT_unit_of_measurement(self):
        """Return the unit of measurement of this entity, if any."""
# this prevent history of state graph
        return TEMP_CELSIUS

    @property
    def is_on(self) -> bool:
        """Return True if DHW is on (albeit limited by thermostat)."""
        _is_on = (self._get_state == DHW_STATES[STATE_ON])
        _LOGGER.debug("is_on(DHWs=%s) = %s", self._id, _is_on)
        return _is_on


    def turn_on(self, **kwargs) -> None:
        """Turn DHW on for an hour, until next setpoint, or indefinitely."""
# TBD: Configure how long to turn on/off for...
        self._set_state(_state = DHW_STATES[STATE_ON], **kwargs)
        _LOGGER.debug("turn_on(DHWs=%s)", self._id)
        return None


    def turn_off(self, **kwargs) -> None:
        """Turn DHW off for an hour, until next setpoint, or indefinitely."""
# TBD: Configure how long to turn on/off for...
        self._set_state(_state = DHW_STATES[STATE_OFF], **kwargs)
        _LOGGER.debug("turn_off(DHWs=%s)", self._id)
        return None


