# Design: Dumb Remote Inference Server + Client-Side Orchestration

- **Date:** 2026-07-24
- **Status:** Draft, pending review
- **Repos involved:** `ZoneMinder/pyzmNg` (primary), `ZoneMinder/zmeventnotificationNg` (secondary)
- **Tracking issue:** _TBD — create on `ZoneMinder/zmeventnotificationNg` before implementation (AGENTS.md rule 2)._ A companion pyzmNg issue is also needed since most code lives there.

---

## 1. Problem

Remote ML detection does not behave like local detection. When `remote.ml_gateway`
is set, a user whose config runs `object,face,alpr` gets **object-only** results,
and their `face`/`alpr`/`model_sequence` config is silently ignored.

### Evidence (observed run, event 202732)

Remote run returned only:

```
Prediction string:[s] detected:(yolov8x) person:94% (yolov8x) car:84%
real 0m2.454s
```

- Model name `yolov8x` is the **server's** model, not the client's configured
  `YOLO ONNX` (yolov8l.onnx).
- 2.4 s wall-clock, **zero** local model loading — nothing ran client-side.
- Face and ALPR never executed.

### Documented promise (which the code does not keep)

- `docs/guides/hooks.rst:539-543`: "All settings … stay in objectconfig.yml …
  The remote server is a pure inference engine … all filtering … is applied
  client-side … using your local config."
- `docs/guides/hooks.rst:568-571`: "your configuration works identically whether
  running locally or remotely."
- `docs/guides/hooks_faq.rst:275-278`: "The remote server is a pure inference
  engine … you configure everything in one place and it works the same."

None of this is implemented today.

## 2. Root cause

### 2a. Primary: the server runs its own pipeline, the client config is inert

- The server (`pyzm/serve/app.py`) builds **one** `Detector` at startup from its
  own `ServerConfig` (`app.py:78-95`) — the models named by `--models`
  (default `["yolo11s"]`, object-only) under `--base-path`. Every request calls
  `detector.detect(image)` on that fixed pipeline (`app.py:150`, `app.py:221`).
- The client's remote path (`pyzm/ml/detector.py:_remote_detect_urls`,
  `detector.py:431-445`) sends only frame URLs, ZM auth, and a few filter params
  (`min_confidence`, `pattern`, `zones`). The client's `model_sequence`, `face`,
  and `alpr` config is **never transmitted**.
- The CLI `--models` only expresses **object** model names — there is no syntax
  for face or alpr. So the default remote server is a single-object detector.
- The server also applies `min_confidence`, `pattern`, and `stop_on_match`
  server-side (`app.py:226-260`), contradicting "all filtering is client-side."

### 2b. Related pre-existing bug: ALPR framework defaults to `opencv`

Independent of remote mode, an ALPR sequence entry configured the documented way
(no `alpr_framework` key) resolves `framework=OPENCV`
(`pyzm/models/config.py:339-343`, `default_fw = "opencv"`), so the pipeline routes
it to the YOLO/darknet backend and it fails to load
(`Error loading model Platerecognizer cloud`, `pipeline.py:131`). The shipped
example (`hook/objectconfig.example.yml:203-217`) also omits `alpr_framework`, so
ALPR is broken locally too. `tests/test_config_variants.py::test_alpr_model_extended_options`
never asserts on `m.framework`, so it passes while the value is wrong.

This is a **separate bug** but should be fixed alongside (or at least tracked from)
this work, because ALPR parity cannot be demonstrated while it is broken.

### 2c. Related gap: `only_check_inside_objects` is unimplemented

`only_check_inside_objects` appears in configs and old docs but is **not wired up**
anywhere in pyzmNg (grep-confirmed). It falls into the ignored `options` dict.
Out of scope for this design; noted so the implementer does not assume it works.

## 3. Goals and non-goals

### Goals

1. Remote detection produces **identical final results** to local detection for
   the same event and the same config ("local ↔ remote parity").
2. The client's `objectconfig.yml` is the **single source of truth** for what runs.
3. The server is **as dumb as possible**: it holds model files, a processor
   setting, and ZM credentials — nothing else. No config parsing, no filtering, no
   frame strategy, no per-monitor state, no push/notification secrets.
4. Fix the docs to describe the real architecture.

### Non-goals

- Implementing `only_check_inside_objects` (tracked separately).
- Multi-tenant model management / hot model uploads.
- Changing local-only detection behavior.
- Audio/BirdNET remote inference (audio stays local; see §7 policy).

## 4. Architecture

**Dumb per-frame inference server + client-side orchestration.**

The seam that makes this work is the already-shared inference primitive
`MLBackend.detect(image) -> list[Detection]` (`pyzm/ml/backends/base.py:35`).
Both local and server paths already converge on it. We relocate *where* the
compute-heavy backends run without duplicating any orchestration or filtering.

### Responsibility split

| Concern | Where | Why |
|---|---|---|
| Read config, resolve secrets / `base_data_path` / monitor overrides | **Client** | Client owns `objectconfig.yml` |
| Decide frame set (`stream_sequence`) | **Client** | Orchestration policy |
| Model sequence order + `pre_existing_labels` gating | **Client** | Orchestration policy (runs in client pipeline) |
| Raw inference for compute-heavy models (object, local face, coral/TPU) | **Server** | The GPU muscle |
| Raw inference for cloud/no-compute models (ALPR cloud, AWS Rekognition) | **Client** | Already just an HTTPS API call; no GPU benefit; avoids shipping API keys to the server |
| Pattern / size / zone filtering | **Client** | Uses client config; server stays dumb |
| Past-detection dedup (`match_past_detections`) | **Client** | Stateful, single-owner per-monitor history |
| Frame strategy (pick best frame) | **Client** | Orchestration policy |
| ZM side-effects: objdetect image, notes, tagging | **Client** | Needs ZM write access |
| Push notifications (FCM) | **Client** | Needs FCM secrets |
| Local fallback when server is down | **Client** | Must have the pipeline locally anyway |
| Fetch frame pixels from ZM | **Server** (URL mode) / **Client** (image mode) | Server uses its own ZM creds; see §5 |

### The parity mechanism (architectural, not just tested)

Refactor `ModelPipeline` so that **producing raw per-model detections for a frame**
is a swappable step, and **everything downstream (gating + filtering) is shared**:

```
ModelPipeline.run(frame):
    raw_by_model = PRODUCE_RAW(frame, enabled_models)   # <-- swappable seam
        local:  for each backend: backend.detect(frame)
        remote: one /infer call per frame with the ordered remote-capable model
                list; cloud/local-only models run in-process; merge, preserving
                config order
    # everything below is identical for local and remote:
    apply pre_existing_labels gating
    apply per-model pattern / size filters
    apply global pattern / size / zone filters
    return detections
```

Because local and remote share every step after `PRODUCE_RAW`, parity is a
structural property, not a coincidence. Tests then *prove* it (see §11).

## 5. Wire contract

The client sends **model references + a frame reference** — never its config. The
server resolves model refs against its own loaded models.

### 5.1 `POST /infer` (primary, URL mode — server fetches)

Request:

```json
{
  "frame": { "eid": 202732, "fid": "snapshot" },
  "models": [
    { "type": "object", "name": "YOLO ONNX" },
    { "type": "face",   "name": "DLIB face recognition" }
  ]
}
```

- `frame.eid` + `frame.fid`: the server builds the ZM image URL from **its own**
  portal config and fetches with **its own** ZM credentials. (Client does not
  forward a ZM token in URL mode.)
- `models`: ordered list; only **remote-capable** models appear here. Cloud/local
  models are omitted (client runs them). `name` is optional; if omitted the server
  uses its single/default model of that `type`.

Response:

```json
{
  "frame_id": "snapshot",
  "image_dimensions": { "original": [2160, 3840] },
  "results": [
    { "type": "object", "name": "YOLO ONNX",
      "detections": [ { "label": "person", "box": [x1,y1,x2,y2], "confidence": 0.93 } ],
      "error": null },
    { "type": "face", "name": "DLIB face recognition",
      "detections": [], "error": null }
  ]
}
```

### 5.2 `POST /infer` (fallback, image mode — client uploads)

Multipart: `image` (JPEG) + `models` (JSON) + `frame_id`. Used when the server
cannot reach ZM. Server does not fetch; it runs inference on the uploaded frame.

### 5.3 `GET /models` (already exists, `app.py:118`)

Advertises `{name, type, framework, loaded}` per model so the client can validate
that the models its config references exist on the server.

### 5.4 Semantics the server MUST honor

- **Raw only.** Return every detection at a permissive confidence floor (NMS still
  runs as part of inference; no user `min_confidence` applied). Apply **no**
  pattern, size, zone, `pre_existing_labels`, or past-detection filtering.
- **No frame strategy.** One frame in, one frame's results out. Frame selection is
  the client's job.
- **No config parsing.** The server never receives or interprets pyzm config.
- **Stateless** except (a) loaded models and (b) an optional short-lived decoded-
  frame cache keyed by `(eid,fid)` to avoid re-decoding when multiple models run on
  the same frame. The cache is a transparent optimization, never policy.

### 5.5 Model addressing & parity constraint

- Client references a model by `type` (+ optional `name`). Server matches to a
  loaded backend. **Unknown ref → `error` populated, `detections: []`.** Client
  logs it and continues, exactly as local does for a model that fails to load.
- **Parity requires the server to have the same model** the client references.
  If the server's `object`/`YOLO ONNX` is a different weights file than the
  client's config, raw detections differ and parity does not hold. This is an
  inherent deployment constraint; document it (§10) and enforce it in the parity
  tests by pointing both sides at the same weights.

## 6. Server changes (pyzmNg — primary)

### 6.1 `pyzm/serve/app.py`

- Replace `/detect` and `/detect_urls` with the new `/infer` (URL + image forms).
  Keep `/health`, `/models`, `/login`.
- Remove all server-side filtering (`min_confidence`, `pattern`, `stop_on_match`,
  zone short-circuit at `app.py:223-261`).
- Add ZM frame fetch using the server's own `ZMClientConfig` (build URL from
  portal + `eid/fid`, auth with server creds, honor `verify_ssl`, reuse the same
  token-refresh/BAD_IMAGE handling the client uses via `pyzm.api`/`pyzm.client`).
- Per request, run **only the requested models** on the one frame and return raw
  per-model detections. Do not run the server's full pipeline blindly.
- Optional decoded-frame LRU keyed by `(eid,fid)` with a few-second TTL.

### 6.2 `pyzm/models/config.py` — `ServerConfig`

- Add ZM connection fields (mirror `ZMClientConfig`): `zm_portal_url`,
  `zm_api_url`, `zm_user`, `zm_password` (SecretStr), `zm_verify_ssl`,
  optional `zm_basic_auth_*`. These are the **only** new secrets the server holds.
- Keep `models`, `base_path`, `processor`, `auth_*`, `workers`. Model files remain
  server-owned. `detector_config` may stay for advanced users but is not required.

### 6.3 `pyzm/serve/__main__.py`

- Add CLI flags for the ZM connection (`--zm-portal`, `--zm-user`,
  `--zm-password`, `--zm-verify-ssl/--no-...`), or require them via `--config`.
- `--models` semantics unchanged (names to load); document that for parity these
  must match what clients reference. `--models all` still auto-discovers.

### 6.4 `pyzm/serve/auth.py`

- Unchanged in shape. Gateway JWT (`/login`) still gates `/infer`.

## 7. Client changes (pyzmNg Detector/pipeline + hook)

### 7.1 `pyzm/ml/pipeline.py` — extract the swappable seam

- Factor `run()` so raw-detection production is one method (`_produce_raw(frame,
  models)`), and gating + filtering are shared downstream (§4 mechanism).
- Local mode: current per-backend `backend.detect(image)` loop.
- Remote mode: batch the enabled **remote-capable** models into one `/infer` call
  per frame; run **client-side** models (cloud ALPR, Rekognition, audio) in-process;
  merge results preserving config order; then run the identical downstream stages.

### 7.2 `pyzm/ml/detector.py`

- Replace `_remote_detect` / `_remote_detect_urls` with a `RemoteInferenceClient`
  used by the pipeline's remote `_produce_raw`. It calls `/infer` (URL form by
  default, image form when configured) and returns raw `Detection`s per model.
- Remove client-side forwarding of `zm_auth` in URL mode (server owns ZM creds).
  (Image-mode path never needed it.)
- Keep `gateway_username`/`password` for the gateway JWT.

### 7.3 `pyzm/ml/backends/*` — remote-capability flag

- Add a class-level `remote_capable` (or a registry) marking:
  - **Remote-capable:** `yolo`/`yolo_onnx`, `coral`, `face_dlib`, `face_tpu`.
  - **Client-only:** `alpr` (cloud API call), `rekognition` (AWS API), `birdnet`
    (audio; stays local).
- The pipeline uses this to decide which models go in the `/infer` list vs run
  in-process.

### 7.4 `hook/zm_detect.py` (this repo)

- The remote-injection block (`zm_detect.py:112-118`) adapts to the new gateway
  config (drop the filter-param forwarding; keep gateway URL/creds/timeout/mode).
- Fallback logic (`zm_detect.py:141-149`) unchanged in intent: on gateway failure
  with `ml_fallback_local: yes`, run fully local. Per-model remote failure inside a
  run should degrade to local for that model (see §9).

## 8. Config changes & checklist

Per AGENTS.md config-key checklist, for every key touched update: `docs/guides/config.rst`,
`hook/objectconfig.example.yml`, `hook/zmes_hook_helpers/common_params.py` (flat keys),
`docs/guides/hooks.rst` examples, pyzmNg docs, and a test.

- **Server (pyzmNg):** new `ServerConfig` ZM fields + CLI flags — document in pyzmNg.
- **Client `remote:` section:** `ml_gateway`, `ml_gateway_mode` (`url` default /
  `image` fallback), `ml_user`, `ml_password`, `ml_timeout`, `ml_fallback_local`
  stay. Remove any now-unused keys. Update `objectconfig.example.yml` and
  `common_params.py`.
- **Fix 2b (ALPR):** default an ALPR-type sequence's framework to
  `plate_recognizer` (or infer from `alpr_service`) in `config.py`; update
  `objectconfig.example.yml` and `docs/guides/config.rst`; add the assertion test.

## 9. Failure handling & fallback

- **Gateway unreachable (connect error, 5xx, timeout):** whole-run fallback to
  local if `ml_fallback_local: yes` (existing behavior). Must yield results
  identical to the pure-local path.
- **Per-model error in an `/infer` response** (`error` set, e.g. unknown model on
  server): client logs and, if `ml_fallback_local: yes`, runs that one model
  locally; otherwise treats it as "model produced no detections" (parity with a
  local model that fails to load). Decision to confirm with maintainer: per-model
  local fallback vs skip. Default proposed: **skip + log**, matching local
  load-failure semantics, with whole-run fallback reserved for transport failure.
- **ZM fetch fails on the server** (frame 404 mid-event): server returns a
  per-frame error; client applies its own `contig_frames_before_error` logic
  client-side (it now owns frame iteration).

## 10. Security

- The server holds **standing ZM credentials**. Recommend a **dedicated,
  least-privilege ZM account** (read-only monitor/event access) rather than admin.
- Gateway JWT auth (`--auth`) should be **on** for any non-loopback deployment.
- Server no longer needs FCM keys, ALPR keys, or `objectconfig` secrets — smaller
  blast radius than a "smart server."
- URL mode requires server→ZM reachability; document the trust boundary.

## 11. Test plan (the centerpiece)

Every test below must **fail before** the change and **pass after** (AGENTS.md
rule 4). Tests live in pyzmNg unless noted. Update `docs/guides/testing.rst`
(this repo) and pyzmNg's test map for any file added/repurposed.

### 11.1 Local ↔ remote parity harness (CROWN JEWEL)

**Location:** pyzmNg `tests/test_ml/test_remote_parity.py` (unit/integration with a
FastAPI `TestClient` server in-process) + an e2e variant in this repo under
`hook/tests/test_e2e/` gated by `ZM_E2E_REQUIRE`.

**Mechanism:** for a fixed frame (or fixed multi-frame set) and a fixed config,
run both paths and assert **equality of final results**:

```
result_local  = Detector.from_dict(cfg).detect(<frames>, zones)
# spin up in-process dumb server (TestClient) with the SAME model files
result_remote = Detector.from_dict(cfg_with_gateway).detect(<frames>, zones)
assert_results_equal(result_local, result_remote)
```

`assert_results_equal` compares: label set + order, box coords (exact for
deterministic models; small tolerance only if a model is non-deterministic),
confidences, detection `type`, `model` name, selected `frame_id`, and the
post-filter surviving set. Both sides point at the **same weights** so raw
detections are identical by construction; the test then proves the client-side
gating/filtering is applied identically.

**Config matrix (one parametrized case each):**
1. object-only
2. object + face (face remote)
3. object + alpr (alpr runs client-side; server never sees it)
4. object + face + alpr (mixed remote/local in one run)
5. with a `pattern` that drops a label
6. with `max_detection_size` that drops a big box
7. with zones that exclude a detection (+ error_boxes)
8. with `pre_existing_labels` gating (alpr only if car present) — assert alpr is
   skipped when no car, run when car present, in BOTH modes
9. multi-frame with each `frame_strategy` (`first`, `most`, `most_models`,
   `most_unique`) — assert the SAME frame is selected in both modes
10. gateway-down → fallback-local equals pure-local (ties §9 to parity)

### 11.2 Server dumbness tests

**Location:** pyzmNg `tests/test_serve/test_infer_endpoint.py`.

- Given raw detections that a pattern/size/zone WOULD remove, `/infer` returns
  them **unfiltered** (server applies no filtering).
- `/infer` runs **only** the requested models, not the server's full model set.
- `/infer` with `frame.eid/fid` triggers exactly **one** ZM fetch per frame even
  when multiple models are requested (assert via a mocked ZM fetcher call count →
  proves the decoded-frame reuse and per-frame batching).
- Server does not require, and ignores, any pyzm config in the payload.
- `min_confidence`/`pattern`/`stop_on_match` fields, if sent by an old client, are
  ignored (no filtering happens server-side).

### 11.3 Model addressing tests

- Known `(type,name)` resolves to the right backend.
- Unknown `name` → `error` populated, `detections: []`; client logs + continues;
  final result parity with a local run where that model fails to load.
- `type` without `name` uses the server's default model of that type.

### 11.4 Remote-capability routing tests

**Location:** pyzmNg `tests/test_ml/test_remote_routing.py`.

- Cloud ALPR is **never** placed in the `/infer` model list; it runs in-process.
  (Assert the outgoing `/infer` payload contains no alpr entry; assert the ALPR
  API client is invoked client-side.)
- object/face are placed in the `/infer` list.
- ALPR API key is **never** transmitted to the server (scan the outgoing request).

### 11.5 ZM-credentials-on-server tests

- Server builds the correct image URL from its own portal config + `eid/fid`.
- Server uses its own creds/token; client sends **no** `zm_auth` in URL mode.
- `verify_ssl` honored.

### 11.6 Fix 2b (ALPR framework) regression

**Location:** pyzmNg `tests/test_config_variants.py` (extend
`test_alpr_model_extended_options`).

- An ALPR sequence without `alpr_framework` resolves
  `m.framework == ModelFramework.PLATE_RECOGNIZER` (fails today → passes after).
- The shipped `hook/objectconfig.example.yml` ALPR entry loads an `AlprBackend`
  (not YOLO/darknet). (This repo, hook test.)

### 11.7 Original-bug regression (end-to-end intent)

**Location:** this repo `hook/tests/test_e2e/` (gated) + a fast integration proxy.

- A remote run with `model_sequence: object,face,alpr` actually executes face
  (remote) and alpr (client-side) — not object-only. Assert the prediction
  includes detections/attempts from all three model types (or explicit skip logs),
  proving the reported bug is fixed.

### 11.8 Contract/schema tests

- `/infer` request and response JSON schemas (both URL and image forms).
- `GET /models` shape unchanged.
- Backward-compat behavior for an old client hitting the new server (documented
  outcome, even if that outcome is a clear error).

### 11.9 pyzm contract test (this repo)

`hook/tests/test_pyzm_contract.py` must be updated to cover the new
`RemoteInferenceClient` / Detector remote surface so cross-repo drift is caught on
every push (AGENTS.md test-gate).

## 12. Documentation to fix

- `docs/guides/hooks.rst:520-571` and `docs/guides/hooks_faq.rst:255-278`: rewrite
  the remote section to describe reality — server is a dumb inference engine that
  needs **model files + a processor + ZM credentials**; the client orchestrates and
  filters; models that run are chosen by the **client** config but must **exist on
  the server**; URL mode (server fetches, needs ZM creds) vs image mode (client
  uploads). Remove the false "pure inference engine / all filtering client-side /
  works identically" phrasing and replace with an accurate parity statement +
  the deployment constraint that the server must have matching model files.
- `docs/guides/config.rst`: ALPR `alpr_framework` guidance (fix 2b).
- pyzmNg docs: `ServerConfig` ZM fields, `/infer` contract, model addressing.

## 13. Backward compatibility & migration

- **Wire break:** `/detect` + `/detect_urls` → `/infer`. Old client ↔ new server
  and vice-versa are incompatible; gate by a server capability in `/health` or a
  version field and fail loudly with an upgrade message rather than silently
  mis-behaving. Document that client and server must be upgraded together.
- Existing server launch commands gain ZM flags; without them, URL mode is
  unavailable and only image mode works (documented).

## 14. Open decisions (confirm before/at implementation)

1. **Per-model remote failure:** skip+log (proposed default) vs per-model local
   fallback. §9.
2. **Model addressing:** `type`+optional`name` (proposed) vs a stricter registry
   id from `GET /models`.
3. **Fix 2b scope:** fix ALPR framework default in this work vs a separate PR
   (recommend: same work, since ALPR parity depends on it).
4. **Decoded-frame cache:** include the small server-side LRU (proposed) or keep
   the server fully stateless and accept re-decode per model.

## 15. Implementation checklist (ordered, for the implementing agent)

1. Create the tracking issue(s) on `ZoneMinder/zmeventnotificationNg` (and pyzmNg);
   label it. Reference in every commit (`refs #<id>`).
2. **pyzmNg:** fix 2b (ALPR framework default) + its test — smallest independent
   win, unblocks ALPR parity.
3. **pyzmNg:** `ServerConfig` ZM fields + CLI flags (§6.2, §6.3) + tests.
4. **pyzmNg:** `/infer` endpoint (URL + image) with server-side ZM fetch; remove
   server filtering (§6.1) + dumbness tests (§11.2, §11.5, §11.8).
5. **pyzmNg:** backend `remote_capable` flags (§7.3) + routing tests (§11.4).
6. **pyzmNg:** pipeline `_produce_raw` seam + `RemoteInferenceClient`; wire remote
   mode (§7.1, §7.2) + addressing tests (§11.3).
7. **pyzmNg:** parity harness (§11.1) — must be green across the full matrix.
8. **zmeventnotificationNg:** hook wiring + fallback (§7.4); example config +
   `common_params.py`; `test_pyzm_contract.py`; e2e regression (§11.7).
9. **Docs:** rewrite remote guides (§12).
10. Run `make gate`; then `make release-gate` (real-pyzm e2e with
    `ZM_E2E_REQUIRE=1`) to prove parity with real models before merge.

---

### Appendix A: worked example (config `object,face`; frames `snapshot,alarm`)

URL mode, per frame the client issues one `/infer`:

```
Client → Server  POST /infer
  { "frame": {"eid":202732,"fid":"snapshot"},
    "models": [ {"type":"object","name":"YOLO ONNX"},
                {"type":"face","name":"DLIB face recognition"} ] }

Server: fetch snapshot via its own ZM creds → decode once →
        run object, then face → return raw per-model detections.

Client: build Detections → run pipeline gating + pattern/size/zone filters
        (identical code to local) → hold frame result.

(repeat once for fid=alarm)

Client: frame_strategy picks best frame → past-dedup → write objdetect.jpg,
        notes, push. Cloud ALPR (if configured) ran in-process, not on the server.
```

Config crosses the wire **zero** times; only model refs + frame refs do. The
server never sees `objectconfig.yml`, never filters, never picks a frame.
