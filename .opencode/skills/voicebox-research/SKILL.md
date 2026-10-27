---
name: voicebox-research
description: Use the voiceBox pipeline to research a topic, capture web and YouTube pages, and create a Zundamon and Metan dialogue with Gemini.
---

Use the repository's `tools/prepare_project.py` for this task. Prepare a request JSON
outside the repository containing the user's `topic`, optional `urls`, approximate
`minutes`, and `provider: "gemini"`. Leave `model` empty for Gemini 3.8 Flash.
Run it with a new output directory:

```
python tools/prepare_project.py --request REQUEST_JSON --output OUTPUT_DIRECTORY
```

The program uses the existing `GEMINI_API_KEY`, searches with Gemini, captures the
pages, and asks Gemini to write the dialogue from the captured sources. Report the
paths to `project.json`, `research.json`, and `materials/`; report any capture
failures from `preparation.json`. Do not print or save credentials. Duration and
source counts are targets, and there is no separate review or approval step.
