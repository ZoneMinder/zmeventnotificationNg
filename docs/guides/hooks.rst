Machine Learning Hooks
======================

.. note::

        This page covers the ML detection pipeline, which is required for **both**
        Path 1 (detection + optional push) and Path 2 (full ES).
        For installation instructions, see :doc:`installation`.
        The hooks use `pyzmNg <https://pyzmng.readthedocs.io/en/latest/>`__ v2
        for detection. Make sure you have ``pyzm`` installed before proceeding.

.. important::

        Setting up hooks requires familiarity with the Linux command line, Python
        package management, and basic troubleshooting. Support is not provided for
        general environment issues (e.g. missing ``pip3``, ``cv2`` import errors).
        The hooks are provided as-is.


Key Features
~~~~~~~~~~~~~

- Detection: objects, faces
- Recognition: faces
- License plate recognition (ALPR) via cloud services
- Audio recognition: bird species identification via BirdNET
- Platforms:

   - CPU (object, face detection, face recognition),
   - GPU (object, face detection, face recognition),
   - EdgeTPU (object, face detection)

- Machine learning can run locally or remotely via ``pyzm.serve``

Requirements
~~~~~~~~~~~~

- Python 3.10+
- OpenCV 4.13+ (for the default ONNX YOLO models)
- pyzmNg v2 (``pip install pyzm``)

How it works
~~~~~~~~~~~~

The main detection script is ``zm_detect.py``. It reads ``objectconfig.yml``,
connects to ZoneMinder, downloads event frames, runs the ML detection pipeline
(via pyzmNg's ``Detector`` API), and returns results.

.. _path1_setup:

Path 1: Detection + optional push (no ES)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Requires ZM 1.38.1+.** ZoneMinder can call ``zm_detect.py`` directly via its
Event Start Command feature — no Event Server needed.

Configure per monitor in ZM: go to the monitor's **Config -> Recording** tab and set:

- **Event Start Command**::

     /var/lib/zmeventnotification/bin/zm_detect.py -e %EID% -m %MID% -r "%EC%" -n --pyzm-debug

  (``-c`` defaults to ``/etc/zm/objectconfig.yml``; pass it explicitly only if your config is elsewhere.)

ZM substitutes ``%EID%``, ``%MID%``, ``%EC%`` tokens at runtime when an event starts.

Detection results are:

- Written to the ZM event notes (``-n`` flag)
- Saved as ``objdetect.jpg`` and ``objects.json`` in the event folder
  (if ``write_image_to_zm: "yes"`` in ``objectconfig.yml``)
- Optionally tagged in ZM (if ``tag_detected_objects: "yes"``, requires ZM >= 1.37.44)
- Optionally sent as **push notifications** via FCM (if ``push.enabled: "yes"`` in
  ``objectconfig.yml``, requires ZM >= 1.39.2 with the Notifications API)

**What you get:** object/face/ALPR/audio detection, annotated images, detection notes in ZM,
local or remote ML via ``pyzm.serve``, and (optionally) FCM push notifications to
registered devices.

**What you don't get:** WebSocket notifications, MQTT,
notification rules/muting, or the ES control interface.

.. note::

   Push notifications in Path 1 require ZoneMinder 1.39.2+ (which adds the
   ``Notifications`` REST API for token storage). Devices register their FCM tokens
   via the ZM API; ``zm_detect`` reads them via pyzmNg and sends push notifications
   through an FCM cloud function proxy after detection. See the ``push`` section
   in ``objectconfig.yml`` for configuration.

To set up Path 1, you only need to:

1. Install pyzmNg and the hooks (see :doc:`install_path1`)
2. Edit ``/etc/zm/objectconfig.yml`` with your ZM portal credentials and desired models
3. Set the **Event Start Command** in the monitor's Config -> Recording tab as shown above
4. Optionally, set **Event End Command** (same tab) to a similar invocation if you want end-of-event processing
5. Optionally, enable push notifications by configuring the ``push`` section in
   ``objectconfig.yml`` (see :ref:`push_config`)

.. _path2_setup:

Path 2: Full Event Server
^^^^^^^^^^^^^^^^^^^^^^^^^^

The ES is a Perl daemon that monitors ZoneMinder's shared memory for new events,
invokes the ML hooks, and handles push notifications, WebSockets, MQTT, rules, and more.

When an event occurs, the ES invokes ``zm_event_start.sh``, which calls ``zm_detect.py``.
Based on the detection result and your notification settings, the ES sends alerts via
FCM (iOS/Android push), WebSockets, MQTT, and/or third-party push APIs.

**What you get (in addition to Path 1):** push notifications to zmNinjaNG and other
FCM clients, WebSocket notifications, MQTT publishing, notification rules (time-based
muting, per-monitor controls), per-device monitor filtering via ``tokens.txt``, and the
ES control interface.

To set up Path 2:

1. Install the ES and its Perl dependencies (see :doc:`install_path2`)
2. Install pyzmNg and the hooks (see :doc:`install_path1`)
3. Edit ``/etc/zm/zmeventnotification.yml`` and ``/etc/zm/objectconfig.yml``
4. Enable ``OPT_USE_EVENTNOTIFICATION`` in ZM ``Options -> Systems``

See :doc:`principles` for a detailed walkthrough of how the ES processes events.

.. note::

   Do **not** configure both ``EventStartCommand`` (Path 1) and the ES hook (Path 2)
   for the same monitors — you would end up running detection twice on every event.

Manual testing
^^^^^^^^^^^^^^^

Regardless of which path you use, you can always test detection manually::

   # Test with a ZM event
   sudo -u www-data /var/lib/zmeventnotification/bin/zm_detect.py \
       --eventid <eid> --monitorid <mid> --debug

   # Test with a local image file (no ZM event needed)
   sudo -u www-data /var/lib/zmeventnotification/bin/zm_detect.py \
       --file /path/to/image.jpg --debug

**Testing push notifications (Direct mode):**

If you have ``push.enabled: "yes"`` in ``objectconfig.yml`` and tokens registered
in the ``Notifications`` table, you can test push delivery from the command line.
Use ``--file`` with ``--eventid`` and ``--monitorid`` to trigger push without a live
event. The ``--fakeit`` flag overrides detection results so you don't need an image
that actually matches your detection pattern::

   sudo -u www-data /var/lib/zmeventnotification/bin/zm_detect.py \
       --file /path/to/any/image.jpg --eventid <eid> --monitorid <mid> \
       --debug --fakeit "person"

Replace ``<eid>`` with a real event ID (so the notification links to a viewable event)
and ``<mid>`` with the monitor ID. Registered devices should receive a push notification
within a few seconds. Check the debug output for ``push:`` log lines to verify delivery.

(``--config`` defaults to ``/etc/zm/objectconfig.yml`` and can be omitted if your config is at the standard path.)


Post install steps
~~~~~~~~~~~~~~~~~~

-  Make sure you edit your installed ``objectconfig.yml`` to the right
   settings. You MUST change the ``general`` section for your own
   portal.
-  If you use ``zm_event_start.sh`` (Path 2), make sure the ``CONFIG_FILE``
   variable in the script matches your config location. When calling
   ``zm_detect.py`` directly (Path 1), ``-c`` defaults to
   ``/etc/zm/objectconfig.yml``.


Test operation
~~~~~~~~~~~~~~

You can test detection directly with ``zm_detect.py`` (no need to go through the shell wrapper):

::

    sudo -u www-data /var/lib/zmeventnotification/bin/zm_detect.py \
        --eventid <eid> --monitorid <mid> --debug

Replace ``<eid>`` with an actual event ID from your ZM console. The ``<mid>`` is the monitor ID
(optional — if specified, monitor-specific settings from ``objectconfig.yml`` will be used).

You can also test with a local image file instead of a ZM event::

    sudo -u www-data /var/lib/zmeventnotification/bin/zm_detect.py \
        --file /path/to/test.jpg --debug

``--config`` defaults to ``/etc/zm/objectconfig.yml``. Pass it explicitly only if your config is elsewhere.

If using the ES hook mode, you can also test the full shell wrapper::

    sudo -u www-data /var/lib/zmeventnotification/bin/zm_event_start.sh <eid> <mid>

If it doesn't work, see :doc:`hooks_faq` for debugging steps.


Upgrading
~~~~~~~~~
To upgrade at a later stage, see :ref:`upgrade_es_hooks`.

Sidebar: Local vs. Remote Machine Learning
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
You can run the machine learning code on a separate server using ``pyzm.serve``
(the built-in remote ML detection server that replaces the legacy ``mlapi``).
This frees up your ZM server resources and keeps models loaded in memory on the GPU box
so subsequent detections are fast. See :ref:`this FAQ entry <local_remote_ml>`.

To start the server on your GPU box::

   pip install "pyzm[serve]"        # or "pyzm[full]" if this box also trains models
   python -m pyzm.serve --models "YOLOv11 ONNX=yolo11l" --port 5000

Then point ``ml_gateway`` in ``objectconfig.yml`` to that server::

   remote:
     ml_gateway: "http://gpu-box:5000"
     ml_fallback_local: "yes"

The server is a dumb inference engine; your ``objectconfig.yml`` drives which
models run, supplies the detection thresholds, and does all filtering.

Two things must line up between the boxes, or results will differ from a local
run without any error being raised:

- **The model name.** A client asks for a model by the ``name`` in its sequence,
  and the gateway answers only to names it publishes — hence the
  ``"YOLOv11 ONNX=yolo11l"`` form above, which loads ``yolo11l`` and publishes it
  under the name the config uses. A name the gateway does not have is skipped
  with an error.
- **The weights behind that name.** Publishing ``yolo11s`` under a name your
  config points at ``yolo11l`` is accepted and runs, but the smaller model scores
  differently, so detections your config would keep locally can fall below your
  threshold remotely.

Face recognition runs entirely on the gateway against its own trained encodings,
so face results match only if both boxes have the same face data.

See :ref:`remote_ml_config` for full details, :ref:`remote-model-names` for
reconciling names, and :ref:`remote-config-ownership` for which box owns which
setting.


.. _supported_models:

Which models should I use?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

- **YOLO ONNX (Recommended)**: The default and recommended model. Uses ONNX format via OpenCV's DNN
  module. Both YOLOv11 and YOLOv26 are supported (both require OpenCV 4.13+).
  Multiple sizes are available for each: ``n`` (nano), ``s`` (small), ``m`` (medium), ``l`` (large)
  — smaller models are faster, larger models are more accurate.
  The default is ``yolo11n`` (nano) which provides a good balance.

- **YOLOv4**: Still supported via Darknet weights. Requires OpenCV 4.4+.

- **Google Coral Edge TPU**: Supported for both object detection and face detection. See install instructions above.

- **YOLOv3 / Tiny YOLOv3 / Tiny YOLOv4**: Still available but no longer installed by default.
  Set the appropriate ``INSTALL_*`` flag to ``yes`` during install if you need them.

- **BirdNET audio recognition**: Identifies 6500+ bird species from audio in ZM events.
  Install via the installer with ``--install-birdnet`` (or ``INSTALL_BIRDNET=yes``),
  or manually: ``/opt/zoneminder/venv/bin/pip install birdnet-analyzer``

- For face recognition, use ``face_model: cnn`` for more accuracy and ``face_model: hog`` for better speed


.. _detection_sequence:

Understanding detection configuration
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

You can chain arbitrary detection types (object, face, alpr, audio) and multiple models within
each type. The detection pipeline is configured through two key structures in ``objectconfig.yml``:

- ``ml_sequence`` — specifies the sequence of ML detection steps
- ``stream_sequence`` — specifies frame selection and retry preferences

.. note::

   All configuration is now in YAML format in ``objectconfig.yml``. The old ``{{variable}}``
   template substitution syntax is **no longer supported**. All values must be specified directly
   in the YAML file. The ``use_sequence`` flag no longer exists — the sequence structures are
   always used.

   The only substitution supported is ``${base_data_path}`` which is replaced with the value from
   ``general.base_data_path``.

Per-monitor overrides
^^^^^^^^^^^^^^^^^^^^^^
If you want to change ``ml_sequence`` or ``stream_sequence`` on a per monitor basis, you can do so
under the ``monitors`` section. You can override the entire structure or just parts of it:

::

   monitors:
     3:
       ml_sequence:
         general:
           model_sequence: "object,face"
         object:
           general:
             pattern: "(person|car)"
     7:
       ml_sequence:
         general:
           model_sequence: "object,alpr"

Per-monitor zones
^^^^^^^^^^^^^^^^^^
You can define detection zones per monitor. Each zone specifies a polygon region and optionally
a ``detection_pattern`` (regex of labels to look for in that zone) and an ``ignore_pattern``
(regex of labels to suppress even if they match ``detection_pattern``).

::

   monitors:
     999:
       zones:
         my_driveway:
           coords: "306,356 1003,341 1074,683 154,715"
           detection_pattern: "(person|car)"
           ignore_pattern: "(car|truck)"
         front_porch:
           coords: "0,0 200,300 700,900"

- ``coords`` — polygon coordinates as ``"x1,y1 x2,y2 x3,y3 ..."``
- ``detection_pattern`` — regex for which labels to accept in this zone (optional; if omitted, all labels match)
- ``ignore_pattern`` — regex for labels to suppress in this zone even if ``detection_pattern`` allows them
  (optional). Useful for excluding parked cars or other stationary objects from a specific area.

You can also import zones from ZoneMinder instead of defining them manually:

::

   general:
     import_zm_zones: "yes"
     only_triggered_zm_zones: "no"



Understanding ml_sequence
^^^^^^^^^^^^^^^^^^^^^^^^^^
The ``ml_sequence`` structure lies in the ``ml`` section of ``objectconfig.yml``.
At a high level, this is how it is structured:

::

   ml:
     ml_sequence:
       general:
         model_sequence: "<comma separated detection types>"
       <detection_type>:
         general:
           pattern: "<pattern>"
           same_model_sequence_strategy: "<strategy>"
         sequence:
           - name: "Model name"
             enabled: "yes"
             # ... model-specific parameters ...
           - name: "Another model"
             enabled: "yes"
             # ... model-specific parameters ...

Here is a concrete example from the default ``objectconfig.yml``:

::

   ml:
     ml_sequence:
       general:
         model_sequence: "object,face,alpr,audio"
       object:
         general:
           pattern: "(person|car|motorbike|bus|truck|boat)"
           same_model_sequence_strategy: first
         sequence:
           - name: YOLO ONNX
             enabled: "yes"
             object_weights: "${base_data_path}/models/ultralytics/yolo11n.onnx"
             object_min_confidence: 0.3
             object_framework: opencv
             object_processor: gpu
       face:
         general:
           pattern: ".*"
           same_model_sequence_strategy: union
         sequence:
           - name: DLIB face recognition
             enabled: "yes"
             face_detection_framework: dlib
             known_images_path: "${base_data_path}/known_faces"
             face_model: cnn
       audio:
         general:
           pattern: ".*"
           same_model_sequence_strategy: first
         sequence:
           - name: BirdNET
             enabled: "yes"
             audio_framework: birdnet
             birdnet_min_conf: 0.5

**Explanation:**

- The ``general`` section at the top level specifies characteristics that apply to all elements inside
  the structure.

   - ``model_sequence`` dictates the detection types (comma separated). Example ``object,face,alpr,audio`` will
     first run object detection, then face, then alpr, then audio

- For each detection type in ``model_sequence``, you specify model configurations in the ``sequence`` list.
  Each entry in the sequence is a model configuration with a ``name`` and ``enabled`` flag.

  **Note**: All ``ml_sequence`` settings (pattern, zones, past-detection filtering, etc.)
  work identically whether detection runs locally or via a remote ``pyzm.serve`` server.
  The remote server is a pure inference engine — all filtering is applied client-side
  using your ``objectconfig.yml``. The exceptions are model file paths, hardware
  choice and face recognition data, which belong to whichever machine runs the
  model; see :ref:`remote-config-ownership`.

Leveraging same_model_sequence_strategy and frame_strategy effectively
'''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''

When using model chaining, these attributes control how aggressively the pipeline searches for matches.

``same_model_sequence_strategy`` is part of ``ml_sequence``  with the following possible values:

   - ``first`` - When detecting objects, if there are multiple fallbacks, break out the moment we get a match
      using any object detection library (Default)
   - ``most`` - run through all libraries, select one that has most object matches
   - ``most_unique`` - run through all libraries, select one that has most unique object matches
   - ``union`` - run through all libraries, combine all detections from every variant into one merged list.
     Useful when you have multiple models that detect different classes (e.g. a base YOLO model and a
     fine-tuned model) and want to combine their results

``frame_strategy`` is part of ``ml_sequence.general`` with the following possible values:

   - 'most_models': Match the frame that has matched most models (does not include same model alternatives) (Default)
   - 'first': Stop at first match
   - 'first_new': Like ``first``, but only counts detections that pass past-detection filtering
     (i.e. genuinely new objects, not parked cars already detected in a prior run)
   - 'most': Match the frame that has the highest number of detected objects
   - 'most_unique': Match the frame that has the highest number of unique detected objects

When two frames tie on the primary metric (e.g. same number of detections), the frame with
the higher total confidence sum wins.
           

**A proper example:**

Take a look at `this article <https://medium.com/zmninja/multi-frame-and-multi-model-analysis-533fa1d2799a>`__ for a walkthrough.

**All options:**

``ml_sequence`` supports various other attributes. See the
`pyzmNg DetectorConfig documentation <https://pyzmng.readthedocs.io/en/latest/source/pyzm.html>`__
for the full list of supported keys (``match_past_detections``, ``past_det_max_diff_area``,
``aliases``, ``max_detection_size``, etc.).

Understanding stream_sequence
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
The ``stream_sequence`` structure lies in the ``ml`` section of ``objectconfig.yml``.
At a high level, this is how it is structured:

::

   ml:
     stream_sequence:
       frame_set: "snapshot,alarm"
       resize: 800
       contig_frames_before_error: 5
       max_attempts: 3
       sleep_between_attempts: 4

**Explanation:**

- ``frame_set`` defines the set of frames it should use for analysis (comma separated)
- ``resize``: resize frames to this width in pixels before detection. Omit to use original resolution.
- ``contig_frames_before_error``: How many contiguous errors should occur before giving up on the series of frames
- ``max_attempts``: How many times to try each frame (before counting it as an error in the ``contig_frames_before_error`` count)
- ``sleep_between_attempts``: When an error is encountered, how many seconds to wait for retrying

**A proper example:**

Take a look at `this article <https://medium.com/zmninja/multi-frame-and-multi-model-analysis-533fa1d2799a>`__ for a walkthrough.

**All options:**

``stream_sequence`` supports various other attributes. See the
`pyzmNg StreamConfig documentation <https://pyzmng.readthedocs.io/en/latest/source/pyzm.html>`__
for the full list (``max_frames``, ``start_frame``, ``frame_skip``, ``save_frames``, etc.).


How ml_sequence and stream_sequence work together
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

The combined logic works as follows:

::

   for each frame in stream sequence:
      perform stream_sequence actions on each frame
      for each model_sequence in ml_options:
      if detected, use frame_strategy (in ml_sequence.general) to decide if we should try other model sequences
         perform general actions:
            for each model_configuration in ml_options.sequence:
               detect()
               if detected, use same_model_sequence_strategy to decide if we should try other model configurations
      

.. _remote_ml_config:

Using the remote ML detection server (pyzm.serve)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. note::

   ``pyzm.serve`` replaces the legacy ``mlapi`` server. It is built into pyzmNg itself,
   uses the same ``Detector`` API, and requires no separate configuration file. The old
   ``mlapiconfig.ini`` is no longer needed.

**What this section covers**, in the order you will need it:

#. :ref:`remote-how-it-works` — what runs on which box.
#. `Server setup (the gateway box)`_ — installing and starting ``pyzm.serve``.
#. `Client setup (the ZM box)`_ — the ``remote:`` section of ``objectconfig.yml``.
#. :ref:`remote-model-names` — **the most common thing to get wrong.** The
   gateway answers only to the model names your config asks for, and face models
   need an explicit ``detector_config`` declaration.
#. :ref:`remote-config-ownership` — which box decides thresholds, paths,
   hardware and face data.
#. `Transports: url vs image`_ — how frames reach the gateway.
#. :ref:`verifying-a-remote-setup` — commands that prove a run went remote,
   and a symptom/cause table.

.. _remote-how-it-works:

How it works
'''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''

The gateway is a **dumb inference engine**. It exposes one endpoint,
``POST /infer``, which runs *one* model on *one* frame and returns raw,
unfiltered detections. Everything else stays on the ZM box: your ``Detector``
runs the model sequence and applies pattern, zone, size and past-detection
filtering plus frame selection, all from your ``objectconfig.yml``.

Only compute-heavy local models are remote-capable (YOLO, Coral TPU, local
face). Cloud ALPR, AWS Rekognition and audio always run on the ZM box — they
are network calls or need local data, so remoting them would only leak your API
keys to the gateway.

Because the same client pipeline runs either way, **local and remote object
detection produce identical results**. Face recognition is the one exception;
see :ref:`remote-config-ownership`.

Server setup (the gateway box)
'''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''

This page covers what a ZoneMinder user needs. The full server reference — every
CLI flag, the YAML schema, ``--models all`` discovery, multi-worker setup and the
API — lives in the
`pyzmNg remote server guide <https://pyzmng.readthedocs.io/en/latest/guide/serve.html>`__.

::

   pip install "pyzm[serve]"

   # Basic usage
   python -m pyzm.serve --models yolo11s --port 5000

Install ``"pyzm[full]"`` instead of ``"pyzm[serve]"`` if this box will also
**train** models — YOLO fine-tuning, or face recognition encodings, which must
be built on whichever box runs the face model. ``[full]`` is ``[ml]`` +
``[serve]`` + ``[train]``, so it covers the server too.

More server options::

   # With authentication
   python -m pyzm.serve --models yolo11s --port 5000 \
       --auth --auth-user admin --auth-password secret

   # Multiple models, GPU inference
   python -m pyzm.serve --models yolo11s yolo26s --port 5000 --processor gpu

Model files must be present **on this box**, under ``--base-path`` (default
``/var/lib/zmeventnotification/models``). The client sends a model *reference*,
never model files or paths.

Everything can also come from a YAML file, which is easier to keep under
configuration management::

   # /etc/zm/pyzm-serve.yml
   host: "0.0.0.0"
   port: 5000
   base_path: "/var/lib/zmeventnotification/models"
   processor: cpu
   models:
     - "YOLOv11 ONNX=yolo11l"

::

   python -m pyzm.serve --config /etc/zm/pyzm-serve.yml

For complete control over each model's settings, use ``detector_config``
instead of ``models``; it accepts full model definitions::

   detector_config:
     models:
       - name: "YOLOv11 ONNX"
         type: object
         framework: opencv
         weights: "/var/lib/zmeventnotification/models/ultralytics/yolo11l.onnx"

Client setup (the ZM box)
'''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''

Install pyzmNg without the ``serve`` extra — the ZM box is only a client::

   pip install pyzm

Then in ``objectconfig.yml``::

   remote:
     ml_gateway: "http://192.168.1.183:5000"
     ml_gateway_mode: "url"
     ml_fallback_local: "yes"
     ml_user: "!ML_USER"
     ml_password: "!ML_PASSWORD"
     ml_timeout: 60

When ``ml_gateway`` is set, ``zm_detect.py`` creates the ``Detector`` in remote
mode. The gateway keeps models loaded in memory, so requests skip the expensive
model-load step. If the gateway is unreachable and ``ml_fallback_local`` is
``yes``, detection falls back to running locally on the ZM box.

.. tip::

   While first setting this up, use ``ml_fallback_local: "no"``. A gateway
   problem then fails loudly instead of quietly running locally and looking
   like everything works. Switch it back to ``"yes"`` once you are happy.

.. _remote-model-names:

Matching model names between client and gateway
'''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''

**A client asks for a model by name.** The ``name`` of each entry in your
``objectconfig.yml`` ``sequence`` must match a name the gateway publishes. If it
does not, that model is skipped with an error like::

   Gateway cannot run YOLOv11 ONNX: no model loaded for type=object
   name='YOLOv11 ONNX'. Load that model on the gateway (pyzm.serve --config),
   or run it locally.

The gateway never substitutes a different model for the one you asked for.
Silently answering with some other model would return detections you did not
request while looking like a successful run.

Ask the gateway what it publishes::

   curl -s http://<gateway>:5000/models | python3 -m json.tool

There are three ways to make the two sides agree. Pick one:

1. **Publish the gateway's model under the client's name (recommended).**
   Write a ``models`` entry as ``<published name>=<spec>``. The gateway loads
   *spec* and answers to *published name*, so no files are renamed and the
   client config stays the source of truth for model identity::

      python -m pyzm.serve --models "YOLOv11 ONNX=yolo11l"

   The same syntax works in a config file::

      models:
        - "YOLOv11 ONNX=yolo11l"
        - "MobileDet=ssdlite_mobiledet_coco_qat_postprocess_edgetpu"

2. **Define the model explicitly** with ``detector_config`` (see above) and set
   its ``name`` to whatever your ``objectconfig.yml`` uses. This is **required**
   for any model without a weights file of its own — see below.

3. **Rename on the client.** Change the sequence entry's ``name`` in
   ``objectconfig.yml`` to a name the gateway already publishes. Fine when you
   have one object model; awkward once several ZM boxes share a gateway.

.. note::

   Names must match, but the *weights behind the name* need not be identical to
   what you would run locally — the gateway may serve a larger model on better
   hardware. Detections will then legitimately differ from a local run. For
   exact local/remote parity, point the published name at the same weights.

The name is only half the key. The gateway matches on **(type, name)**, and the
type comes from which sequence your entry sits in — ``object``, ``face``,
``alpr`` or ``audio``. A model the gateway registered as type ``object`` is
unreachable from your ``face`` sequence no matter how well the names line up.
``curl http://<gateway>:5000/models`` reports both halves.

.. important::

   **Face models cannot be declared in the gateway's ``models`` list.**
   That list is a filename shorthand: each entry is looked up on disk and the
   type and framework are inferred from the file found. Every path through it
   produces ``type: object`` — there is no branch that yields ``face``.

   This is why a gateway can serve YOLO perfectly while failing on faces.
   ``yolo11l`` finds ``yolo11l.onnx``, and the ``.onnx`` extension alone
   establishes both facts the gateway needs, so nothing more has to be said.
   dlib face recognition has no weights file at all — it runs off
   ``known_images_path`` and the encodings trained from it — so a bare
   ``"DLIB face recognition"`` entry matches nothing, falls back to
   ``type: object`` with no weights, and fails to load. A TPU face
   ``.tflite`` is worse: it loads fine, as an *object* model, and is then
   unreachable from your ``face`` sequence.

   Declare it under ``detector_config`` with an explicit type and framework::

      detector_config:
        models:
          - name: "YOLOv11 ONNX"
            type: object
            framework: opencv
            processor: gpu
            weights: "/var/lib/zmeventnotification/models/ultralytics/yolo11l.onnx"

          - name: "DLIB face recognition"
            type: face
            framework: face_dlib
            known_faces_dir: "/var/lib/zmeventnotification/known_faces"
            unknown_faces_dir: "/var/lib/zmeventnotification/unknown_faces"
            face_model: cnn

   ``detector_config`` **replaces** the ``models`` list — when it is present,
   ``models``, ``base_path`` and ``processor`` at the top level are ignored, so
   each model needs its own absolute ``weights`` path and ``processor``.

   The gateway also needs ``dlib`` and ``face_recognition`` importable (not
   included in the ``[serve]`` extra — see ``[full]``), and the face images
   themselves in ``known_faces_dir`` **on the gateway**, since that is where
   the encodings are trained.

   Full field reference for ``detector_config``, and the table of what each
   file extension registers as, are in
   `Declaring models on the gateway <https://pyzmng.readthedocs.io/en/latest/guide/serve.html#declaring-models-two-forms-and-when-each-works>`__.

Once the names line up, check that the rest of your settings land where you
expect: see :ref:`remote-config-ownership` for which box decides what, and
:ref:`verifying-a-remote-setup` for the commands that prove a run went remote.

.. _remote-config-ownership:

Which side owns which setting
'''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''

Every setting has exactly one owner. The other side has no say in it. There is
no merging and no overriding, so nothing silently wins.

**The ZM box owns the outcome.** These come from your ``objectconfig.yml`` and
travel with each request, so they apply identically to local and remote runs:

- ``object_min_confidence`` (and the per-type ``*_min_confidence`` keys)
- ``pattern``, zone definitions, ``max_detection_size``
- ``model_sequence``, ``same_model_sequence_strategy``, ``frame_strategy``
- past-detection filtering, ``pre_existing_labels`` gating

Change a threshold on the ZM box and the next event uses it. The gateway needs
no restart and has no say in the value.

**The gateway owns the machine.** These come from how you started
``pyzm.serve`` and cannot be set from ``objectconfig.yml`` for a remote run:

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Setting
     - Why the gateway owns it
   * - ``object_weights``, ``object_config``, ``object_labels``, ``--base-path``
     - Filesystem paths. A ZM-box path is meaningless on the gateway, so paths
       never cross the wire in either direction.
   * - ``--processor`` (cpu/gpu/tpu)
     - Hardware the gateway actually has.
   * - Model input dimensions
     - A property of the weights file loaded on the gateway.
   * - ``known_images_path``, ``unknown_images_path``
     - Face encodings and unknown-face crops live where the face model runs.

**Face recognition runs entirely on the gateway.** It matches against the
gateway's trained encodings and writes unknown-face crops to the gateway's
disk; the ZM box receives only the recognised name. This means:

- Train faces **on the gateway**, not on the ZM box. Encodings built on the ZM
  box are never consulted for a remote run.
- ``known_images_path``, ``unknown_images_path``, ``face_train_model``,
  ``face_num_jitters``, ``face_upsample_times``, ``face_recog_dist_threshold``,
  ``save_unknown_faces`` and ``save_unknown_faces_leeway_pixels`` in your
  ``objectconfig.yml`` apply to **local** runs only. With ``ml_gateway`` set,
  the gateway's own values are used.
- Face results therefore need not match between a local and a remote run — the
  two machines have different face databases. Object detection parity is exact;
  face parity depends on you keeping the encodings in sync.

Transports: url vs image
'''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''

Set by ``ml_gateway_mode``:

- ``url`` (default) — the ZM box sends frame references and the **gateway**
  fetches each frame directly from ZoneMinder. Nothing downloads on the ZM box.
- ``image`` — the ZM box fetches frames and uploads them as lossless PNG. Use
  when the gateway cannot reach your ZM portal.

``url`` mode needs the gateway to reach your ZM portal directly. Verify from the
gateway box before relying on it::

   curl -sI https://your-zm-host/zm/index.php | head -1

Two situations fall back to ``image`` automatically, per event, and log the
reason:

- **A client-side model is enabled** (cloud ALPR, audio). Those need local
  pixels, so the frames have to be downloaded anyway.
- **A** ``resize`` **is set in** ``stream_sequence``. The gateway fetches frames
  from ZM at full resolution and never sees your resize, so staying in ``url``
  mode would run inference on different pixels than a local run.

Server endpoints
'''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''

- ``GET /health`` — returns ``{"status": "ok", "models_loaded": true}``
- ``GET /models`` — lists published models (``name``, ``type``, ``framework``,
  ``loaded``). This is the list your client's model names must match.
- ``POST /infer`` — ``type`` (+ optional ``name``) plus either an uploaded
  ``image`` (image mode) or ``url`` + ``zm_auth`` (url mode). Also accepts
  ``min_confidence``, which replaces the threshold the gateway loaded the model
  with, so your config's value applies remotely. Returns
  ``{"detections": [...], "error": null}`` — otherwise raw and unfiltered.
  A ``name`` the gateway has not loaded is an error, never a substitution.
- ``POST /login`` — accepts ``{"username": ..., "password": ...}``, returns a
  JWT token.

.. _verifying-a-remote-setup:

Verifying a remote setup
'''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''

On the gateway::

   curl -s localhost:5000/health
   curl -s localhost:5000/models
   curl -s -F type=object -F image=@/path/to/some.jpg -F min_confidence=0.15 \
        http://localhost:5000/infer

Then run one event on the ZM box::

   sudo -u www-data /var/lib/zmeventnotification/bin/zm_detect.py \
     --config /etc/zm/objectconfig.yml --eventid <EID> --monitorid <MID> --debug

It is working when the gateway logs ``POST /infer HTTP/1.1" 200 OK`` once per
model per frame, and the ZM box logs no ``Remote failed (...), falling back to
local``. In ``url`` mode the ZM box also logs no ``index.php?view=image``
fetches — those requests reach your portal from the gateway's IP instead.

.. list-table::
   :header-rows: 1
   :widths: 45 55

   * - Symptom
     - Cause
   * - ``404`` on ``POST /infer``, ``/login`` works
     - The gateway runs a pyzm older than the dumb-gateway rework. Upgrade it
       and restart the process — a running server keeps the old code in memory.
   * - ``Gateway cannot run <name>``
     - The gateway has no model with that (type, name). Either the names differ,
       or the model registered under the wrong type — a face model listed in the
       gateway's ``models`` shorthand registers as ``object``. See
       :ref:`remote-model-names`.
   * - Gateway logs ``cannot determine an origin framework``
     - A ``models`` entry matched no file on disk and fell back to
       ``type: object`` / ``framework: opencv`` with no weights. Declare that
       model under ``detector_config``. Newer pyzm reports this directly.
   * - A detection appears locally but not remotely
     - Check the gateway's ``dropping <label> (x < y)`` line. ``y`` should be
       your configured ``object_min_confidence``; if it is not, the ZM box is
       running an older pyzm that does not send the threshold.
   * - Frames download on the ZM box despite ``url`` mode
     - A ``resize`` or a client-side model forced ``image`` mode for that event.
       The reason is logged.

Here is a part of my config, for example:

::

   general:
     import_zm_zones: "yes"

   monitors:
     3:
       # doorbell
       ml_sequence:
         general:
           model_sequence: "object,face"
         object:
           general:
             pattern: "(person|monitor_doorbell)"
     7:
       # Driveway
       ml_sequence:
         general:
           model_sequence: "object,alpr"
         object:
           general:
             pattern: "(person|car|motorbike|bus|truck|boat)"
     2:
       # Front lawn
       ml_sequence:
         general:
           model_sequence: "object"
         object:
           general:
             pattern: "(person)"
     4:
       # deck
       ml_sequence:
         object:
           general:
             pattern: "(person|monitor_deck)"
       stream_sequence:
         frame_set: "alarm"
         resize: 800
         contig_frames_before_error: 5
         max_attempts: 3
         sleep_between_attempts: 4

About specific detection types
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

License plate recognition
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Three ALPR options are provided: 

- `Plate Recognizer <https://platerecognizer.com>`__ . Uses a deep learning model that provides more accurate results than OpenALPR in my testing. Requires a license key (a `free tier <https://platerecognizer.com/pricing/>`__ is available with 2500 lookups per month).
- `OpenALPR <https://www.openalpr.com>`__ . While OpenALPR's detection is not as good as Plate Recognizer, when it does detect, it provides a lot more information (like car make/model/year etc.)
- `OpenALPR command line <http://doc.openalpr.com/compiling.html>`__. This is a basic version of OpenALPR that can be self compiled and executed locally. It is far inferior to the cloud services and does NOT use any form of deep learning. However, it is free, and if you have a camera that has a good view of plates, it will work.

``alpr_service`` defined the service to be used.

Face Dection & Recognition
^^^^^^^^^^^^^^^^^^^^^^^^^^^
When it comes to faces, there are two aspects (that many often confuse):

- Detecting a Face
- Recognizing a Face 

Face Detection 
'''''''''''''''
If you only want "face detection", you can use either dlib/face_recognition or Google's TPU. Both are supported.
Take a look at ``objectconfig.yml`` for how to set them up.

Face Detection + Face Recognition
'''''''''''''''''''''''''''''''''''

Face Recognition uses
`this <https://github.com/ageitgey/face_recognition>`__ library. Before
you try and use face recognition, please make sure you did a
``/opt/zoneminder/venv/bin/pip install face_recognition`` The reason this is not
automatically done during setup is that it installs a lot of
dependencies that takes time (including dlib) and not everyone wants it.

.. sidebar:: Face recognition limitations

        Overhead cameras will not work well. This library requires a
        reasonable face orientation (works for front facing, or somewhat side
        facing poses) and does not work for full profiles or completely overhead
        faces. Take a look at the `accuracy
        wiki <https://github.com/ageitgey/face_recognition/wiki/Face-Recognition-Accuracy-Problems>`__
        of this library to know more about its limitations. Note that ``cnn`` mode is significantly more accurate than ``hog`` mode, but comes with a speed and memory tradeoff.

Using the right face recognition modes
'''''''''''''''''''''''''''''''''''''''

- Face recognition uses dlib. Note that in ``objectconfig.yml`` you have two options of face detection/recognition. Dlib has two modes of operation (controlled by ``face_model``). Face recognition works in two steps:
  - A: Detect a face
  - B: Recognize a face

``face_model`` affects step A. If you use ``cnn`` as a value, it will use a DNN to detect a face. If you use ``hog`` as a value, it will use a much faster method to detect a face. ``cnn`` is *much* more accurate in finding faces than ``hog`` but much slower. In my experience, ``hog`` works ok for front faces while ``cnn`` detects profiles/etc as well. 

Step B kicks in only after step A succeeds (i.e. a face has been detected). The algorithm used there is common irrespective of whether you found a face via ``hog`` or ``cnn``.

Configuring face recognition directories
''''''''''''''''''''''''''''''''''''''''''

-  Make sure you have images of people you want to recognize in
   ``/var/lib/zmeventnotification/known_faces``
- You can have multiple faces per person
- Typical configuration:

:: 

  known_faces/
    +----------bruce_lee/
                +------1.jpg
                +------2.jpg
    +----------david_gilmour/
            +------1.jpg
            +------img2.jpg
            +------3.jpg
    +----------ramanujan/
            +------face1.jpg
            +------face2.jpg


In this example, you have 3 names, each with different images.

- It is recommended that you now train the images by doing:

::

  sudo -u www-data /var/lib/zmeventnotification/bin/zm_train_faces.py


If you find yourself running out of memory while training, use the size argument like so:

::

     sudo -u www-data /var/lib/zmeventnotification/bin/zm_train_faces.py --size 800

   
   
- Note that you do not necessarily have to train it first but I highly recommend it. 
  When detection runs, it will look for the trained file and if missing, will auto-create it. 
  However, detection may also load yolo and if you have limited GPU resources, you may run out of memory when training. 

-  When face recognition is triggered, it will load each of these files
   and if there are faces in them, will load them and compare them to
   the alarmed image

known faces images
''''''''''''''''''
-  Make sure the face is recognizable
-  crop it to around 800 pixels width (doesn't seem to need bigger
   images, but experiment. Larger the image, the larger the memory
   requirements)
- crop around the face - not a tight crop, but no need to add a full body. A typical "passport" photo crop, maybe with a bit more of shoulder is ideal.


Audio Recognition (BirdNET)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^

BirdNET identifies 6500+ bird species from audio extracted from ZoneMinder events.
It uses the `BirdNET-Analyzer <https://github.com/kahst/BirdNET-Analyzer>`__ deep learning
model. Audio is extracted from the event video, split into 3-second chunks, and each chunk
is analyzed for bird species. The best confidence per species across all chunks is reported.

**Installation:**

BirdNET is not installed by default. Use the installer flag::

   sudo -H ./install.sh --install-birdnet

Or install manually into the venv::

   /opt/zoneminder/venv/bin/pip install birdnet-analyzer

**Configuration** (in ``objectconfig.yml``):

1. Add ``audio`` to your ``model_sequence``::

      ml_sequence:
        general:
          model_sequence: "object,face,alpr,audio"

2. Configure the ``audio`` section::

      audio:
        general:
          pattern: ".*"
          same_model_sequence_strategy: first
        sequence:
          - name: BirdNET
            enabled: "yes"
            audio_framework: birdnet
            birdnet_min_conf: 0.5
            birdnet_lat: -1
            birdnet_lon: -1
            birdnet_sensitivity: 1.0
            birdnet_overlap: 0.0

**Parameters:**

- ``birdnet_min_conf`` — minimum confidence threshold (0.0–1.0). Default: 0.5
- ``birdnet_lat``, ``birdnet_lon`` — latitude/longitude for seasonal species filtering.
  Set to your location to restrict predictions to species expected in your area at the
  current time of year. -1 disables location filtering (all 6500+ species considered).
  If set to -1 but the ZM monitor has lat/lon in the database, those values are used
  as a fallback.
- ``birdnet_sensitivity`` — sigmoid sensitivity (higher = more sensitive, more false positives). Default: 1.0
- ``birdnet_overlap`` — overlap in seconds between consecutive 3-second audio chunks (0.0–2.9). Default: 0.0

**Notes:**

- Audio detection only runs on monitors that have audio recording enabled in ZoneMinder.
- Unlike image-based detections, audio detections do not have spatial bounding boxes
  (a dummy 1×1 box is used internally).
- BirdNET results appear in event notes alongside object/face/ALPR detections.
- The ``pattern`` regex in the ``audio`` general section filters species names,
  e.g. ``pattern: "(Robin|Sparrow)"`` to only report specific species.


Troubleshooting
~~~~~~~~~~~~~~~
See :doc:`hooks_faq` for troubleshooting, debugging, and common issues.

zm_detect.py command-line reference
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

::

    zm_detect.py [-h] [-c CONFIG] [-e EVENTID] [-p EVENTPATH] [-m MONITORID]
                 [-v] [--bareversion] [-o OUTPUT_PATH] [-f FILE] [-r REASON]
                 [-n] [-d] [--fakeit LABELS] [--pyzm-debug]
                 [-O KEY=VALUE [KEY=VALUE ...]]

``-c, --config``
    Path to ``objectconfig.yml`` (default: ``/etc/zm/objectconfig.yml``).

``-e, --eventid``
    ZM event ID to analyze (required unless ``--file`` is given).

``-f, --file``
    Skip event download, detect on a local image file instead.

``-m, --monitorid``
    Monitor ID. Enables per-monitor overrides (zones, ml_sequence, etc.) from config.

``-p, --eventpath``
    Path to store output files (``objdetect.jpg``, ``objects.json``).

``-r, --reason``
    Reason/cause string for the event (passed by ZM or the ES).

``-n, --notes``
    Update ZM event notes with detection results.

``-d, --debug``
    Print debug logs to terminal.

``--fakeit LABELS``
    Override detection with fake labels for testing (comma-separated).
    Example: ``--fakeit "dog,person"``

``--pyzm-debug``
    Route pyzm library internal debug logs through ZMLog.

``-v, --version``
    Print version and exit.

``--bareversion``
    Print just the version number (no pyzm version) and exit.

``-o, --output-path``
    Directory to write debug images to (used with ``write_debug_image``).

``-O, --override KEY=VALUE``
    Override any config value from ``objectconfig.yml`` via dot-notation paths.
    Repeatable — specify once per override. Applied after all other config
    loading (including per-monitor overrides), so this is the highest-priority
    override.

    **Flat keys:**

    .. code-block:: bash

        zm_detect.py -e 12345 -m 1 -O show_percent=20 -O write_debug_image=yes

    **Nested keys** (use dots to traverse dicts):

    .. code-block:: bash

        zm_detect.py -e 12345 -m 1 -O ml_sequence.object.general.pattern="(car|person)"

    **List indexing** (use ``[N]`` for sequence entries):

    .. code-block:: bash

        zm_detect.py -e 12345 -m 1 -O ml_sequence.object.sequence[0].object_min_confidence=0.5

    **Name-based lookup** (use ``[Name]`` to match by the ``name`` field, case-insensitive):

    .. code-block:: bash

        zm_detect.py -e 12345 -m 1 -O "ml_sequence.object.sequence[YOLOv11 ONNX].enabled=no"
        zm_detect.py -e 12345 -m 1 -O "ml_sequence.audio.sequence[BirdNET].birdnet_min_conf=0.3"

    Values are auto-coerced: integers and floats are parsed as numbers,
    ``yes``/``no`` remain as strings. Invalid paths log a warning and are
    skipped.

Questions
~~~~~~~~~~~
See :doc:`hooks_faq`
