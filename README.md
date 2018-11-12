# Home Assistant Custom Component for Honeywell Evotouch

_News: I am now working towards getting this component accepted into HA; this is the `custom_component` version, which I plan to keep up-to-date until all (most?) of its functionality in accepted into HA. As of 2018/11/12, only the controller (and not the zones, DHW controller) exist as a HA component._

Support for Honeywell (EU-only) Evohome installations: one controller, multiple heating zones and (optionally) a DHW controller.  It provides _much_ more functionality that the existing Honeywell climate component 

You _could_ run it alongside the existing `honeywell.py` component (why woudl you want to?). To run it in tandem with the builtin `evohome.py`componenet, youd'd have to change its name (instructions here: https://github.com/zxdavb/evohome/issues/10).  

Includes support for those of you with multiple locations.  Use can choose _which_ location with `location_idx:`, and you can even have multiple concurrent locations with the following work-around: https://github.com/zxdavb/evohome/issues/10

NB: this is _for EU-based systems only_, it will not work with US-based systems (it can only use the EU-based API).

-DAVB

## Installation instructions

You must be running HA v0.80.0 or later (it has an updated evohomeclient).  Make the following changes to your exisiting installation of HA:
 1. Download this git into the `custom_components` folder (which is under the folder containing `configuration.yaml`) by executing something like: `git clone https://github.com/zxdavb/evohome.git custom_components` (currently, there are 3 files to download)
 2. When required, update the git by executing: `git pull` (this git can get very frequent updates).
 3. Edit `configuration.yaml` as below.  I recommend 300 seconds, and `high_precision: true` (both are defaults). YMMV with heuristics/schedules.
 
You will need to redo 1) only once if you use `git`.  You will need to redo 2) as often as the git is updated. You will need to do 3) only once.

### Post-Installation checklist

The system shoudl start, and show: the Controller, your Zones, and your DHW (if any).  If there is a problem, either
a) you see nothing: first check your `configutation.yaml` - start with only username & password & go from there
b) you see only the controller: installation is invalid and HA is using the builtin component, try the instructions here: https://community.home-assistant.io/t/refactored-honeywell-evohome-custom-component-eu-only/59733/182

### Troubleshooting

Try the following:
  `cat home-assistant.log | grep evohome | grep ERROR`, and/or
  `cat home-assistant.log | grep evohome | grep WARN`, and/or
  `cat home-assistant.log | grep evohome | grep Found`

## Configration file

The `configuration.yaml` is as below (note `evohome:` rather than `climate:` & `- platform: honeywell`).  
```
evohome:
  username: !secret evohome_username
  password: !secret evohome_password

# These config parameters are presented with their default values...
# scan_interval: 300     # seconds, you might get away with 180
# high_precision: true   # temperature in tenths instead of halves
# location_idx: 0        # if you have more than 1 location, use this

# These config parameters are YMMV...
# use_heuristics: false  # this is for the highly adventurous person, YMMV
# use_schedules: false   # this is for the slightly adventurous person
# away_temp: 15.0        # °C, if you have a non-default Away temp
# off_temp: 5.0          # °C, if you have a non-default Heating Off temp
```
If required, you can add logging as below (make sure you don't end up with two `logger:` directives).
```
# These are for debug logging...
logger:
  logs:
    custom_components.evohome: debug
    custom_components.climate.evohome: debug
#   evohomeclient2: warn
```

## Improvements over the existing Honeywell component

1. Uses v2 of the (EU) API via evohome-client: several minor benefits, but v2 temperature precision is reduced from .1C to .5C), so...
2. Optionally leverages v1 of the API to _increase_ precision of reported temps to 0.1C (actually the API reports to 0.01C, but HA only displays 0.1); transparently falls back to v2 temps if unable to get v1 temps. NB: you can't do this if you have >1 location - see work-around issue/#10.
3. Exposes the controller as a separate entity (from the zones), and...
4. Correctly assigns operating modes to the controller (e.g. Eco/Away modes) and it's zones (e.g. TemporaryOverride/PermanentOverride modes) - although zones state is set to controllers operating mode if it is in FollowSchedule mode.
5. Greater efficiency: loads all entities in a single `add_devices()` call, and uses many fewer api calls to Honeywell during initialisation/polling.
6. The DHW is exposed: its `current_temperature` can be read and it's `operating_mode` can bet set.
7. Much better reporting of problems communicating with Honeywell's web servers via the client library - entities will report themselves a 'unavailable' (`self.available = True`) in such scenarios.
8. If the API rate limit is exceeded, the component will implement a backoff algorithm.
9. Much better reporting of issues with `_LOGGER.warnings()` in `home-assistant.log`.
9. Other stuff I've forgotten.

## Problems with current implemenation

1. It takes about 60-180 seconds for the client api to accurately report changes made elsewhere in the location (usu. by the Controller).  This is a sad fact of the Internet Polling architecture & nothing can be done about it.
2. The controller, which doesn't have a `current_temperature` is implemented as a climate entity, and HA expects all climate entities to report a temperature.  This causes problems with HA, and so it displays an average of all its zones' current/target temperatures.
3. Away mode (as understood by HA), is not fully implemented as yet.  HA has difficulties with more 'complex' climate entities (this is under review). Away mode is available via the controller.
4. No provision for changing schedules (yet).  This is for a future release.
5. DHW is WIP.  Presently, there is no 'boiler' entity type in HA.

## Notes about `scan_interval`

The `scan_interval` parameter defaults to 180 secs, but could be as low as 60 secs.  This is OK as this code polls Honeywell servers only 1x (or 3x) per scan interval (there is +2 polls for v1 temperatures), or 60 per hour (plus a few more once hourly).  This compares to the existing evohome implementation, which is at least one poll per zone per scan interval.  

I understand that up to 250 polls per hour is considered OK, YMMV.
