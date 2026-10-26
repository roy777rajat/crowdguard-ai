# Meta Model API integration

Status: **implemented for text explanations, off by default, not yet run against the live API.**
Code: `backend/integrations/meta_adapter.py`, selection in `backend/config.py`, prompt in
`backend/observability/prompts.py`, pricing and retry rules in `backend/observability/llm.py`.

## What it does

When enabled, each new incident (and each "Explain current state" click) sends Meta one request containing a fixed
instruction plus a JSON summary of recorded, simulated measurements (pandal name, occupancy, capacity, risk,
5- and 10-minute forecast, confidence, rule name). **No images, video or personal data are sent.** The reply is
checked automatically: every number in it must appear in the recorded values, otherwise the call is flagged
"ungrounded". The reply can never change occupancy, forecasts, risk or alarms.

## Enabling it

Both conditions are required, so a key sitting in the environment alone never causes outside calls:

1. `CROWDGUARD_PROVIDER=meta`
2. `META_MODEL_API_KEY=<key>` (or Meta's documented name `MODEL_API_KEY`)

Put them in `E:\dev\crowdguard-ai\.env` (git-ignored) or in real environment variables, then restart the backend.
If the flag is set but the key is missing or the model name is invalid, the app falls back to the local template
provider and says why under Settings. The key is never logged, stored in the database, or returned by any API.
Optional: `META_MODEL` = `muse-spark-1.3` (default), `muse-spark-1.2` or `muse-spark-1.1`.

## Verified against Meta's documentation (read 25 Oct 2026)

Sources: https://dev.meta.ai/docs/overview, /docs/models, /docs/protocols/responses,
/docs/api-reference/responses/create-response, /docs/api-reference/responses/schemas, /docs/error-handling,
/docs/pricing-rate-limits.

- `POST https://api.meta.ai/v1/responses`, header `Authorization: Bearer <key>`.
- Request fields used: `model`, `instructions`, `input`, `max_output_tokens` (minimum 16; we send 1500), `store`
  (we send `false`), `reasoning.effort` (we send `minimal`; `none` is unsupported). The model reasons before it answers
  and reasoning tokens count against `max_output_tokens`; a first live test with 600 tokens ended `incomplete` for that
  reason.
- Response: `status`, optional `output_text`, `output[]` items (`message` items hold `output_text` parts; `reasoning`
  items are skipped), `usage.input_tokens` and `usage.output_tokens`.
- Errors: `{"error": {"message", "type", "param", "code"}}`. Retried: 429, 500, 503 (honouring `Retry-After`, with a
  10 s cap so a long wait fails fast instead of stalling the workflow). Not retried: 400, 401, 402, 403, 404, 413, 504.
- Prices per 1M tokens, Standard mode: input $1.25, output $4.25 for all three Muse Spark models. The pricing page
  shows no update date, so the app records `meta-standard-2026-10-25` as its pricing version.
- Rate limits are per team: 3,000 requests and 4,000,000 tokens per minute for Standard models. This app uses a tiny
  fraction.

## Deliberate choices and caveats

- Only Standard models are allowed. **Contributor variants are rejected** because their data-use terms were not
  reviewed; they are much cheaper, which suggests different terms. Read Meta's terms before ever allowing them.
- Cost is an **estimate**: cached-input discounts are not applied, and `usage` may omit fields (then cost is `null`).
- Failed calls keep any usage Meta reported (an `incomplete` reply is still billed), so spend is not understated.
- A model budget (Settings) blocks further real calls once estimated spend reaches it. It cannot see spend made
  outside this app or calls whose token counts were not reported.
- Incident explanations run automatically only when an incident is first opened (repeats are deduplicated), but a
  fast simulation across many pandals still makes real, billable calls. Keep a budget set.
- `store=false` asks Meta not to retain the response; Meta's own logging and retention terms still apply.

## Not done yet

- SAM 3.1 (`sam-3.1`): needs real images or video, a privacy review and a validated counting pipeline. Meta's docs
  show it is called through the same Responses endpoint, with media uploaded via `POST /v1/files`, but the request
  body was not available in the pages read. Do not feed segmentation counts into alarms until validated against
  ground truth.
- Streaming, tool calling, image or video input, and prompt caching are not used.
- A live test: the adapter has only been tested against a mocked HTTP transport.

## AI Assistant (Muse Spark tool calling) and Muse Voice Transcribe

Added on 2026-10-26. Details, the full tool list and limits are in [assistant-tools.md](assistant-tools.md).

* `MetaModelProvider.respond_with_tools` extends the existing adapter (same credential, model whitelist, error
  categories and `store:false` stance). It sends flat function tools with `tool_choice:"auto"` and
  `include:["reasoning.encrypted_content"]` so reasoning items can be replayed statelessly.
* `backend/assistant/transcribe.py` calls `POST https://api.meta.ai/v1/asr/transcribe` (multipart `request` + WAV `audio`,
  mode `ENDPOINTING` by default so long questions with pauses are joined; `PUSH_TO_TALK` via `META_VOICE_MODE`). The realtime WebSocket API is documented by Meta but not used.
* Every assistant model step is logged in the model-call table (`operation = assistant_chat`) with latency, tokens
  and estimated cost; tokens/cost stay `null` when Meta does not report them. Prompts and audio are not stored.
* Verified against mocked Meta responses only. It has not been exercised against the live Meta API in this build.
