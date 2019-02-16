# Changelog for the Honeywell evohome custom component

This is a Home Assistant `custom_component` that supports Honeywell evohome multi-zone heating systems (EU-only).

## 0.0.0 - BREAKING Change

0.0.0 Change to new scheme for HA v0.88 - 'Integrations need to be in their own folder.'
    PR #20677 - Embed all platforms into components
    PR #21023 - Update file header
    PR #20945 - Climate const.py move (and also Water Heater const.py)
    
    This is a BREAKING change if you use this with HA <0.88 and I am not sure what happens if you use the old scheme of evohome_cc with HA => 0.88.

## 0.9.x - WIP

0.9.5 Remove some logspam.  Change name of TCS for compliance with HA slugify rules.

0.9.2 Initial support for [custom_updater](https://github.com/custom-components/custom_updater/wiki/Installation) platform.  Many improvements to exception handling (ignoring v1 api exceptions, warnings for ConnectionErrors).

