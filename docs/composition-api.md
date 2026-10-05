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
again. This format has no dedicated manual recovery action yet. Preserve the
held job's evidence and use a separate, deliberate new submission only after
deciding to render again. If the initial submission response is lost, inspect
Queue before submitting another request.
