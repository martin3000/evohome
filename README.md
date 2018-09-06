# Home Assistant Custom Component for Honeywell Evotouch

_News: I am now working towards getting this component accepted into HA; this is the `custom_component version`, which I plan to keep up-to-date._

Support for Honeywell (EU-only) Evohome installations: one controller, multiple heating zones and (optionally), DHW.

It provides _much_ more functionality that the existing Honeywell climate component does not and you may be able to run it alongside that component (but see below).

This is beta-level code, YMMV.

NB: this is _for EU-based systems only_, it will not work with US-based systems (it can only use the EU-based API).

-DAVB

## Installation instructions (have recently changed)

You can make the following changes to your exisiting installation of HA:
 1. Change the `REQUIREMENTS` in /components/honeywell.py to be: `'evohomeclient==0.2.7'` (instead of `0.2.5`) - this will not affect the functionality of that component. 
 2. Download this git into the `custom_components` folder (which is under the folder containing `configuration.yaml`) by executing something like: `git clone https://github.com/zxdavb/evohome.git custom_components`
 3. Update the git by executing: `git pull`
 4. Edit `configuration.yaml` as below.  I recommend trying 60 seconds, and high_precision, but YMMV with heuristics/schedules.
 
You will need to redo 1) only after upgrading HA to a later/earlier version.  You will need to do 2) only once.  You will need to redo 3) as often as the git is updated. You will need to do 4) only once.

## Configration file

The `configuration.yaml` is as below (note `evohome:` rather than `climate:` & `- platform: honeywell`)...
```
evohome:
  username: !secret evohome_username
  password: !secret evohome_password
# scan_interval: 180    # how often to poll api, rounded up to nearest 60 seconds, minimum is 60
# high_precision: true  # use additional api calls for PRECISION_TENTHS rather than PRECISION_HALVES
# use_schedules: false  # long story, but much slower initialisation & other downsides...
# use_heuristics: false # trys to update state without waiting fro next poll of the api
# location_idx: 0       # if you have more than one location

```

## Improvements over the existing Honeywell component

This list is not up-to-date...

1. Uses v2 of the (EU) API via evohome-client: several minor benefits, but v2 temperature precision is reduced from .1C to .5C).
2. Leverages v1 of the API to increase precision of reported temps to 0.1C (actually the API reports to 0.01C, but HA only handles 0.1); transparently falls back to v2 temps if unable to get v1 temps. 
3. Exposes the controller as a separate entity (from the zones), and...
4. Correctly assigns operating modes to the controller (e.g. Eco/Away modes) and it's zones (e.g. FollowSchedule/PermanentOverride modes)
5. Greater efficiency: loads all entities in a single `add_devices()` call, and uses fewer api calls to Honeywell during initialisation/polling.
6. The DHW is exposed.


## Problems with current implemenation

0. It takes about 60-180 seconds for the client api to accurately report changes made elsewhere in the location (usu. by the Controller). 
1. The controller, which doesn't have a `current_temperature` is implemented as a climate entity, and HA expects all climate entities to report a temperature.  So you will see an empty temperature graph for this entity.  A fix will require: a) changing HA (to accept a climate entity without a temperature (like a fan entity), or; b) changing the controller to a different entity class (but this may break some of the away mode integrations planned for the future).
2. Away mode (as understood by HA), is not implemented as yet - however, you can use service calls to `climate.set_operation_mode` with the controller or zone entities to set Away mode (as understood by evohome).
6. No provision for changing schedules (yet).  This is for a future release.
7. The `scan_interval` parameter defaults to 300 secs, but could be as low as 60 secs.  This is OK as this code polls Honeywell servers only 1x (or 3x) per scan interval (is +2 polls for v1 temperatures), or 60 per hour (plus a few more once hourly).  This compares to the existing evohome implementation, which is at least one poll per zone per scan interval.  I understand that up to 250 polls per hour is considered OK, YMMV.
8. DHW is represented as a switch (with an operating mode) and a switch (for temp).  Presently, there is no 'boiler' entity type in HA.
