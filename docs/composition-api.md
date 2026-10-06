# Blender and Editor composition API

A composition renders one or two typed Blender animations, then assembles up
to eight clip instances into an MP4. Several clips can reuse one rendered
segment with different frame ranges. The finished video appears in the
project's Gallery and can be opened in Editor.

Blender renders on the Maestro computer. Editor assembly uses CPU FFmpeg.
The output has a silent 48 kHz AAC track. This first format supports scene
objects and keyframes; it does not accept executable code, imported files,
camera or lighting instructions, or generated sound.

Start Maestro through Pinokio and wait for Blender to report ready. Use an
authenticated session with permission to edit and generate in the selected
project. Local and authorized remote sessions use the same project API.

## In-app repeats

Open **Tools → Blender** and set the object, color, start and end positions,
Seconds and FPS. Choose **2–8 Repeats**, then select **Render repeats**. Use a
whole-number FPS from 1–120 and at least two motion frames. Maestro resets
the current Blender scene and renders the authored motion once at 1280×720,
then joins the repeats with silent audio. The sequence uses the selected
project and privacy setting and requires both editing and generation access.
Director does not visually review this sequence.

“Added to Queue” confirms submission. Follow progress in Queue; when the
video finishes, open its Gallery entry in Editor. If the response is lost or
cannot be confirmed, check Queue before another submission.

For a Director-authored scene, enter its description and select **Plan scene
only**. **Queue planned repeats** uses all objects and keyframes in that plan,
including rotation and scale. It derives duration from the inclusive planned
frame range and uses the plan's FPS; manual motion controls do not change it.
Choose the repeat count and privacy setting before submission. This path skips
Director visual review and does not create a kept motion-guide candidate.

## Package

Save this example as `composition.json`. It is a two-second blue cube
animation used twice, producing a four-second video at 24 fps.

```json
{
  "schema": "maestro/composition/v1",
  "id": "blue-cube-twice",
  "canvas": {"width": 128, "height": 72, "fps": 24},
  "audio": {"mode": "silence", "sample_rate": 48000},
  "segments": [{
    "id": "cube-motion",
    "scene": {
      "clear_scene": true,
      "objects": [{"name": "Cube", "primitive": "cube",
        "material": {"name": "Blue", "color": [0, 0, 1, 1]}}]
    },
    "animation": {
      "frame_start": 0,
      "frame_end": 47,
      "objects": [{"name": "Cube", "keyframes": [
        {"frame": 0, "location": [0, 0, 0]},
        {"frame": 47, "location": [2, 0, 0]}
      ]}]
    },
    "fps": 24,
    "width": 128,
    "height": 72
  }],
  "clips": [
    {"id": "first-use", "segment_id": "cube-motion", "source_frame": 0, "frame_count": 48},
    {"id": "second-use", "segment_id": "cube-motion", "source_frame": 0, "frame_count": 48}
  ]
}
```

Each clip ID is unique. `source_frame` is an offset within the rendered
segment, starting at zero even when the segment's animation starts at another
Blender frame. `frame_count` must fit the segment. Blender frame ranges are
inclusive. Segment frame rates are integers from 1 to 240; the final canvas
rate can be fractional from 1 to 120. Each clip is rounded to the final
encoder frame clock before joining. Canvas and segment dimensions must be
even: width 64–7680 and height 64–4320. Each segment is limited to 7,200 frames.

`id` identifies the package in its provenance; submitting it again creates
another job. It is not an idempotency key.

## Submit and follow the job

Send `POST /api/v1/projects/{project}/compositions` with exactly `package`
and the boolean `private_output`. The response contains `job_id` and
`status`. Follow `GET /api/v1/status/{job_id}` or the Queue tab until the
job completes. Private controls preview blur; project access remains enforced.

JavaScript, from the signed-in Maestro page's origin:

```javascript
async function submitComposition(packageData, project = 'my-project') {
  const response = await fetch(`/api/v1/projects/${encodeURIComponent(project)}/compositions`, {
    method: 'POST', credentials: 'same-origin',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({package: packageData, private_output: true})
  });
  if (!response.ok) throw new Error(`Composition submission failed (${response.status})`);
  const {job_id} = await response.json();
  const status = await fetch(`/api/v1/status/${job_id}`, {credentials: 'same-origin'});
  return {job_id, status: await status.json()};
}
// Pass the parsed composition.json to submitComposition once.
```

Python, using an authenticated cookie jar you manage:

```python
import http.cookiejar
import json
import urllib.parse
import urllib.request

base = "https://YOUR-MAESTRO-ORIGIN"
project = "my-project"
cookies = http.cookiejar.MozillaCookieJar("cookies.txt")
cookies.load(ignore_discard=True)
client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookies))
with open("composition.json", encoding="utf-8") as source:
    package = json.load(source)
request = urllib.request.Request(
    base + "/api/v1/projects/" + urllib.parse.quote(project, safe="") + "/compositions",
    data=json.dumps({"package": package, "private_output": True}).encode(),
    headers={"Content-Type": "application/json", "Origin": base}, method="POST",
)
with client.open(request, timeout=30) as response:
    job = json.load(response)
with client.open(base + "/api/v1/status/" + job["job_id"], timeout=30) as response:
    print(json.load(response))
```

Curl, using the active origin and authenticated cookie jar from the README's
API examples:

```bash
python3 - <<'PY' > composition-request.json
import json
with open("composition.json", encoding="utf-8") as source:
    print(json.dumps({"package": json.load(source), "private_output": True}))
PY
curl -fsS -b cookies.txt -H "Origin: $BASE" -H 'Content-Type: application/json' \
  --data-binary @composition-request.json \
  "$BASE/api/v1/projects/my-project/compositions"
# Use the returned job_id; do not submit again to check progress.
curl -fsS -b cookies.txt "$BASE/api/v1/status/JOB_ID"
```

## Cancellation and recovery

Use the Queue's normal Cancel action. A cancellation that wins before job
completion prevents a finished output from being published. Completed
segments may be reused after an interruption only when their signed receipt
and exact bytes still match the saved request. A fully prepared delivery can
be recovered without rendering again, even if Blender is temporarily unavailable.

If a Blender attempt ends without a verified completed segment, the job stays
held for review. Maestro does not automatically run that unknown attempt
again. When Queue offers **Review composition recovery**, review the unfinished
render and choose **Confirm recovery** to request a new attempt on the same job.
Maestro first verifies that the previous worker, Blender render, and encoder
have stopped. It verifies and reuses completed segments, preserves the original
attempt privately, and renders the unfinished segments in a fresh directory.
The package, project, and privacy setting remain bound to the original job.

Recovery stays held when that stop proof is missing or ambiguous, including
older attempts without a saved process identity. Changed completed bytes or
receipts also prevent recovery. A confirmation requests these checks; it does
not guarantee that a new render will be admitted.
When the bounded recovery-request history is full, Queue explains that no
further recovery requests can be accepted. The original evidence remains saved.

The browser saves the recovery request ID before submitting. After a lost
acknowledgement or page reload, **Check recovery status** looks up that same
request without submitting another attempt. Changing accounts or leaving the
composition prevents an earlier response from triggering a new request.
If the initial composition submission response is lost, inspect Queue before
submitting another composition.

### Recovery API

Use an authenticated session with the original job's project permissions.
First read `GET /api/v1/status/{job_id}`. Only a job advertising
`recover_composition` in `recovery_actions` is eligible for confirmation.
Persist one UUID4 and the exact request body before the first POST:

```json
{
  "recovery_request_id": "b6a6c2ec-fc34-4197-9008-e07bbaf27910",
  "expected_created_at": 1000,
  "expected_execution_attempt": 2,
  "confirmed": true
}
```

Replace the example timestamp and attempt with `created_at` and
`composition_recovery_execution_attempt` from the current job status. Send the
saved body once to `POST /api/v1/queue/{job_id}/composition-recovery`.
The response contains `job_id`, a canonical hyphenated `recovery_request_id`,
`status` (`pending`, `accepted`, or `rejected`), and a plain-language `message`.
The stop proof runs asynchronously; `pending` means the request is saved and
its result has not yet been decided. `accepted` means a new attempt was
admitted, rather than that the composition finished.

Check that exact request with
`GET /api/v1/queue/{job_id}/composition-recovery/{recovery_request_id}`. This GET
neither probes Blender nor dispatches rendering. A missing response, timeout,
or missing lookup result is not proof of rejection: keep the saved ID and use
GET again. Duplicate POSTs with the same exact logical request reconcile its
receipt; a changed body using the same ID is rejected. Only a positively
rejected request and a fresh explicit confirmation allow another request for
the same interrupted execution. A later interrupted execution requires its
new current attempt number and a new confirmation.

Previously confirmed pending requests resume their checks after an app restart.
This does not automatically repeat an armed Blender attempt. A valid request
rejected before admission has its own saved receipt, so clients can confirm the
rejection through GET before reviewing another request. At the request-history
limit, existing IDs remain readable; an unknown ID is not added and its GET
remains missing. Refresh job status for the Queue's limit explanation.
Jobs with saved recovery requests survive automatic completed-job compaction
and startup retirement, retaining their scoped receipt lookup. Explicitly
dismissing the original job ends that lookup guarantee.
