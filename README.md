<p align="center">
  <img src="logo/zmesng.png" alt="zmES Ng Logo" width="200">
</p>

Note
-----
This is a newer version of ES (7.x) that I'm redoing for zmNinjaNG support and better features.
Read about what is [different](CHANGES.md). 

What
----
The Event Notification Server sits along with ZoneMinder and offers real time notifications, support for push notifications as well as Machine Learning powered recognition.
As of today, it supports:
* detection of 80 types of objects (persons, cars, etc.) 
* face recognition
* deep license plate recognition

Documentation
--------------

Full documentation — installation, configuration, testing, and more — is on **Read the Docs**:

**[zmeventnotificationng.readthedocs.io](https://zmeventnotificationng.readthedocs.io/en/latest/)**

Key pages:
- [Installation](https://zmeventnotificationng.readthedocs.io/en/latest/guides/installation.html)
- [Configuration](https://zmeventnotificationng.readthedocs.io/en/latest/guides/config.html)
- [Testing](https://zmeventnotificationng.readthedocs.io/en/latest/guides/testing.html)
- [Hooks & ML](https://zmeventnotificationng.readthedocs.io/en/latest/guides/hooks.html)

ES 7.0 is in development — expect breakages. If you find issues, please post them to this repo, not ZM repos.

Developer Notes (for myself)
----------------------------
To run ALL tests (ES + pyzm):
```
make test-all
```
Runs the ES suite (perl + hook + tools) and the pyzm suite (unit, including
local↔remote parity). Fast, no models or ZM needed. Point at a non-default
pyzm checkout with `make test-all PYZM_SRC=/path/to/pyzmNg`.

To run EVERYTHING including e2e (real models + live ZM):
```
make test-all-e2e
```
Adds ES e2e and pyzm e2e — this is what runs the **real-model** local↔remote
parity. Environment required:
- ML model files at `/var/lib/zmeventnotification/models` (override:
  `PYZM_E2E_MODELS=/path`)
- a live ZoneMinder (DB + config under `/etc/zm`) for pyzm's `test_zm_e2e`
- run as the ZM user for ZM access, e.g. `sudo -u www-data make test-all-e2e`

Missing prerequisites FAIL (not skip) because the e2e gates set
`ZM_E2E_REQUIRE=1` / `PYZM_E2E_REQUIRE=1`.

To make a release:
```
./scripts/make_release.sh
```

To test docs:
```
cd docs/
make html && python -m http.server -d _build/html
```

To test a CHANGELOG:
```
# VERSION in project root should be updated
# replace v7.0.0 with whatever future version
GITHUB_TOKEN=$(gh auth token) git-cliff --tag "v7.0.0"
```


Requirements
-------------
- Python 3.10 or above

Screenshots
------------

Click each image for larger versions. Some of these images are from other users who have granted permission for use
###### (permissions received from: Rockedge/ZM Slack channel/Mar 15, 2019)

<img src="https://github.com/ZoneMinder/zmeventnotificationNg/blob/master/screenshots/person_face.jpg" width="300px" /> <img src="https://github.com/ZoneMinder/zmeventnotificationNg/blob/master/screenshots/delivery.jpg" width="300px" /> <img src="https://github.com/ZoneMinder/zmeventnotificationNg/blob/master/screenshots/car.jpg" width="300px" /> <img src="https://github.com/ZoneMinder/zmeventnotificationNg/blob/master/screenshots/alpr.jpg" width="300px" />
