Testing
========

The zmeventnotificationNg project has three tiers of tests:

- **Perl** unit tests for the Event Server (``zmeventnotification.pl`` and the
  ``ZmEventNotification/*.pm`` modules).
- **Python unit / integration** tests for the ML hook (``zm_detect.py``,
  ``zmes_hook_helpers/*``) and the ``tools/`` config utilities. These mock
  pyzmNg and need no models.
- **Python end-to-end (e2e)** tests that run real YOLO models through the full
  config-to-detection chain.

None of the tests require a running ZoneMinder instance.


Running all tests
------------------

.. code-block:: bash

   cd zmeventnotificationNg

   # Perl tests
   prove -I t/lib -I . -r t/

   # Python unit tests (hook + tools), no models needed
   cd hook && python3 -m pytest tests/ -m "not e2e" -q && cd ..
   python3 -m pytest tools/tests/ -q

   # Python e2e tests (needs real pyzmNg + models, see below)
   cd hook && python3 -m pytest tests/test_e2e/ -q && cd ..


Unit / integration tests
-------------------------

These tests mock pyzmNg and use no real models.  They run anywhere:

.. code-block:: bash

   # Perl
   prove -I t/lib -I . -r t/

   # Python (hook unit/integration)
   pip install pytest pyyaml
   cd hook && python3 -m pytest tests/ -m "not e2e" -v

   # Python (tools)
   python3 -m pytest tools/tests/ -v


End-to-end tests
-----------------

The ``hook/tests/test_e2e/`` directory contains tests that exercise
the full objectconfig YAML → pyzmNg detection → output chain using real
YOLO models and a real test image (``bird.jpg``, included in the repo).

These tests use **real pyzmNg** (installed as a system library, not
mocked) and call the same functions that ``zm_detect.py`` uses:
``process_config()`` → secret substitution → ``Detector.from_dict()`` →
``detector.detect()`` → ``format_detection_output()``.

**Prerequisites:**

- pyzmNg importable (``pip install pyzm`` or from source). If pyzmNg is on a
  non-standard path, run pytest with ``PYTHONPATH=/path/to/pyzmNg``.
- ML models at ``/var/lib/zmeventnotification/models/``
  (at least one YOLO model, e.g. ``yolov4/`` or ``ultralytics/``)
- Python packages: ``opencv-python``, ``numpy``, ``shapely``

**Run all e2e tests:**

.. code-block:: bash

   cd hook
   python3 -m pytest tests/test_e2e/ -v

**Run a single test file:**

.. code-block:: bash

   python3 -m pytest tests/test_e2e/test_pattern_config.py -v

**Strict mode for CI (``ZM_E2E_REQUIRE=1``)**

By default the e2e suite *skips* when prerequisites (models, ``bird.jpg``, or a
real pyzmNg import) are missing, so local dev runs stay green. That skip would
silently hide a regression on a CI runner that lacks the models. Set
``ZM_E2E_REQUIRE=1`` to turn a missing prerequisite into a hard **failure**
instead of a skip:

.. code-block:: bash

   # CI: fail (do not silently skip) if models/pyzm are unavailable
   ZM_E2E_REQUIRE=1 python3 -m pytest tests/test_e2e/ -q


Perl test reference
-------------------

.. list-table::
   :header-rows: 1
   :widths: 35 65

   * - File
     - What it covers
   * - ``t/00-constants.t``
     - Constant definitions
   * - ``t/01-config-get-val.t``
     - ``config_get_val`` secrets/yes-no/template substitution/trimming
   * - ``t/02-util.t``
     - ``Util`` helpers, ``buildPictureUrl``
   * - ``t/03-rules.t``
     - Rules matching
   * - ``t/04-hook-processor-logic.t``
     - HookProcessor decision logic
   * - ``t/05-contract-split-format.t``
     - Hook output ``--SPLIT--`` contract
   * - ``t/06-fcm-payload.t``
     - FCM v1 payloads: proxy + **direct service-account** branches, the
       **stacked per-event tag** (``zmninja_<eid>_<type>``), replace-mode tag,
       ``tag_alarm_event_id``, ``include_profile_in_push``, TTL/channel
   * - ``t/07-mqtt-payload.t``
     - MQTT payload construction
   * - ``t/08-websocket-payload.t``
     - WebSocket payload construction
   * - ``t/09-send-event-routing.t``
     - ``sendEvent`` channel routing
   * - ``t/10-should-send-event.t``
     - ``shouldSendEventToConn`` gating
   * - ``t/11-db-operations.t``
     - DB tagging / user-id lookup
   * - ``t/12-fcm-token-file.t``
     - FCM token file read/write/migrate
   * - ``t/13-connection.t``
     - Connection accounting
   * - ``t/14-mqtt-init.t``
     - MQTT initialization
   * - ``t/15-websocket-handler.t``
     - WebSocket message handling and **``validateAuth`` password comparison**
       (bcrypt ``$2b$``/``$2y$`` normalization, MySQL ``password41``, ``-ZM-``
       guard)
   * - ``t/16-fcm-auth.t``
     - Google service-account JWT / access token
   * - ``t/17-version.t``
     - Version string
   * - ``t/18-edge-cases.t``
     - Assorted edge cases
   * - ``t/19-fork-orchestration.t``
     - HookProcessor fork helpers: ``_build_alarm_obj``,
       ``_tag_detected_objects`` (both JSON shapes), ``_run_api_push`` gating


Python unit / integration reference
-----------------------------------

.. list-table::
   :header-rows: 1
   :widths: 35 65

   * - File
     - What it covers
   * - ``tests/test_main_handler.py``
     - End-to-end ``zm_detect.main_handler`` orchestration with recording
       fakes: ``zm_conf_path`` → ``ZMClient``, ``--debug`` log elevation,
       gateway injection + local fallback, **URL-mode frame fetch through
       ``zm.api.request``**, ``--fakeit``, note update, object tagging, push
       dispatch
   * - ``tests/test_push.py``
     - ``send_push_notifications``: monitor filtering, throttle, picture-URL
       rewrite, Android/iOS payloads, invalid-token deletion heuristic
   * - ``tests/test_cli_overrides.py``
     - ``-O`` dot-notation overrides: coercion, ``[index]`` / ``[name]`` paths,
       error/edge cases
   * - ``tests/test_config_flow.py``
     - Monitor/remote config loading, deep-merge, ``import_zm_zones`` passthrough,
       coords-less zones recorded for the ZM name join
   * - ``tests/test_utils_config.py``
     - ``process_config`` config resolution and zone parsing
   * - ``tests/test_output_format.py``
     - ``format_detection_output`` string/JSON formatting
   * - ``tests/test_utils_helpers.py``
     - ``str2tuple`` and helper functions
   * - ``tests/test_config_errors.py``
     - ``process_config`` error/exit paths
   * - ``tests/test_import_zm_zones.py``
     - Zone import filtering (active/triggered), and the name-based join of config
       patterns onto ZM zone geometry
   * - ``tests/test_zm_detect.py``
     - CLI argparse early-exits (version, missing config)


Tools test reference
--------------------

.. list-table::
   :header-rows: 1
   :widths: 35 65

   * - File
     - What it covers
   * - ``tools/tests/test_config_migrate.py``
     - ``config_migrate_yaml`` INI→YAML: variable-chain expansion, monitor/zone
       parsing, polygon detection, type coercion
   * - ``tools/tests/test_config_upgrade.py``
     - ``config_upgrade_yaml`` managed defaults / removed keys
   * - ``tools/tests/test_deep_merge.py``
     - ``deep_merge`` user-value-wins, nested add, dict/scalar mismatch
   * - ``tools/tests/test_es_config_migrate.py``
     - ``es_config_migrate_yaml`` template + secrets migration
   * - ``tools/tests/test_config_edit.py``
     - ``config_edit`` parse/apply, comment-out, ``_global_`` cross-section
   * - ``tools/tests/test_install_doctor.py``
     - ``install_doctor`` model discovery, OpenCV-version + model classification


e2e test reference
------------------

.. list-table::
   :header-rows: 1
   :widths: 35 65

   * - File
     - What it covers
   * - ``test_config_to_detect.py``
     - Full config→detect→output chain, output format, show_percent,
       image in matched_data, ``${base_data_path}`` substitution
   * - ``test_secret_chain.py``
     - ``!TOKEN`` secret substitution in model paths and general config fields
   * - ``test_pattern_config.py``
     - Restrictive pattern (no matches), broad pattern (matches),
       specific label-only pattern
   * - ``test_confidence_config.py``
     - High threshold demonstrably drops detections vs low, low keeps
   * - ``test_disabled_config.py``
     - ``enabled=no`` produces nothing, mixed enabled/disabled
   * - ``test_monitor_config.py``
     - Per-monitor ml_sequence override, real zone parsing into ``g.polygons``,
       per-monitor config key override (show_percent)
   * - ``test_multi_model_config.py``
     - UNION strategy (both models present), FIRST strategy (first model only)
   * - ``test_zone_config.py``
     - Full-image zone keeps detections, tiny zone filters all,
       zone with restrictive pattern
   * - ``test_format_chain.py``
     - JSON roundtrip validity, label-confidence correlation,
       bounding box coordinates valid


Test dependencies
------------------

.. list-table::
   :header-rows: 1
   :widths: 20 80

   * - Tier
     - Dependencies
   * - Perl
     - ``Test::More``, ``YAML::XS``, ``JSON``, ``Time::Piece``
   * - Python (unit)
     - ``pytest``, ``pyyaml``
   * - Python (e2e)
     - all of the above plus ``pyzmNg``, ``opencv-python``, ``numpy``, ``shapely``


Pytest markers and environment
------------------------------

.. list-table::
   :header-rows: 1
   :widths: 25 75

   * - Marker / env var
     - Description
   * - ``e2e`` (marker)
     - End-to-end tests requiring real pyzmNg, models, and images on disk
   * - ``ZM_E2E_REQUIRE=1``
     - Turn missing e2e prerequisites into a hard failure instead of a skip
       (use in CI)
