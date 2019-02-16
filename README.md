> ### Warning
>
> This custom component has been re-written for HA's new scheme/structure for custom components from version 0.88/0.89 onwards - it will likely not work with old versions of HA.
> 
> |**Old Folder Structure**|**New Folder Structure**
> |---|---
> ```custom_components/evohome_cc.py```|```custom_components/evohome_cc/__init__.py```
> ```custom_components/climate/evohome_cc.py```|```custom_components/evohome_cc/climate.py```
> ```custom_components/water_heater/evohome_cc.py```|```custom_components/evohome_cc/water_heater.py```
> 
> Notice also that the `logger:` and `custom_updater:` sections of your `configuration.yaml` may need updating. 

## HA Custom Component for Honeywell evohome

This is a Home Assistant `custom_component` that supports **Honeywell evohome** multi-zone heating systems (EU-only).  It _will not_ work with US-based systems (it is written to utilize the EU-based API only, see: https://github.com/watchforstock/evohome-client).

It supports a Honeywell evohome controller with multiple heating zones and (optionally) a DHW controller.  

You can choose _which_ location with `location_idx:` (most people will have only one location), and you can even have multiple _concurrent_ locations/logins with the following work-around: https://github.com/zxdavb/evohome/issues/10

#### Other Versions of this Component

This is the `custom_component` version of HA's official evohome component (see: https://home-assistant.io/components/evohome).  It may include functionality that is not yet - or will never be - supported by HA.  There are good reasons why you may choose to run this _concurrently_ with the official version (see below for more detail).

You _could_ even run it alongside HA's older `honeywell` component (see: https://home-assistant.io/components/climate.honeywell), although I believe there is little reason for doing so.

### Installation instructions

You must be running HA v0.88 / v0.89 (TBA) or later - it has a new scheme for custom_components directory stuctures.  

Make the following changes to your existing installation of HA:
 1. Download this git into the `custom_components` folder (which is under the folder containing `configuration.yaml`) by executing something like: `git clone https://github.com/zxdavb/evohome.git evohome_cc`
 2. Edit `configuration.yaml` as below.  I recommend 300 seconds, and `high_precision: true` (both are defaults). YMMV with heuristics/schedules.
 3. To keep your component up to date, there are two options: a) manually (e.g. via `git pull`), or b) using the custom component updater (https://github.com/custom-components/custom_updater), which is preferred.
 
#### Post-Installation checklist

TBD

#### Troubleshooting

Execute this command: `cat home-assistant.log | grep WARNING | grep evohome`, and you should expect to see the following warning, `You are using a custom component for evohome_cc`:
```
2018-11-06 16:30:33 WARNING (MainThread) [homeassistant.loader] You are using a custom component for evohome_cc which has not been tested by Home Assistant. This component might cause stability problems, be sure to disable it if you do experience issues with Home Assistant.
```

If you don't see this, then something is wrong with your `custom_components` folder, or your `configuration.yaml`.

Regardless of that you can also try the following:
  `cat home-assistant.log | grep evohome | grep ERROR`, and/or
  `cat home-assistant.log | grep evohome | grep WARN`, and/or
  `cat home-assistant.log | grep evohome | grep Found`

### Configuration file

The `configuration.yaml` is as below (NB: it is `evohome_cc:` rather than `evohome:`)

```
evohome_cc:
  username: !secret evohome_username
  password: !secret evohome_password

# These config parameters are presented with their default values...
# scan_interval: 300     # seconds, you might get away with 120
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
    custom_components.evohome_cc: debug
    custom_components.evohome_cc.climate: debug
    custom_components.evohome_cc.water_heater: debug
#   evohomeclient2: warn
```

#### Notes about `scan_interval` and `high_precision`

The `scan_interval` parameter defaults to 300 secs, but could be as low as 120 secs.  This _should be_ OK as this component polls Honeywell servers with only 1 API call per scan interval, with a maximum 30 per hour (plus a few more once hourly for authentication/authorization).

However, Note that `high_precision` temps use 3 API calls per scan interval for a maximum of 90 per hour.

I understand that up to 250 polls per hour is considered OK, but YMMV (if anyone has any official info on this, I'd like to know).

### List of Possible Future Changes (WIP)

Replace AutoWithEco: mode that allows a delta of +/-0.5, +/-1.0, +/-1.5, etc.

Improve heuristics: detect TRV Off, and OpenWindow

