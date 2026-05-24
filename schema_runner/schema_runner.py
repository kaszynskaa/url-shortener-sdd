"""
Schema-Enforced Claude API Interaction — Part 3.3

Demonstrates using the Anthropic SDK with an inline JSON schema to
constrain Claude's output to a valid UrlResponse object.

REQ: Part 3.3 — at least one Claude interaction must use an inline JSON schema.

Strategy:
  We use Claude's tool_use feature to enforce structured output.
  A tool named "emit_url_response" is defined with the UrlResponse JSON schema
  as its input_schema. Claude is instructed to ONLY call this tool, never
  respond in free text. This guarantees the output matches the schema.

Usage:
  python -m schema_runner.schema_runner

Output:
  schema_runner/validated_output.json  — Claude's response, schema-validated.

Dependencies:
  pip install anthropic jsonschema
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import anthropic
import jsonschema

# ── Paths ─────────────────────────────────────────────────────────────────────
HERE = Path(__file__).parent
SCHEMA_FILE = HERE / "output_schema.json"
OUTPUT_FILE = HERE / "validated_output.json"

# ── Load the JSON schema (REQ: Part 3.3 — documented schema) ─────────────────
with SCHEMA_FILE.open() as f:
    URL_RESPONSE_SCHEMA: dict = json.load(f)

# ── Tool definition  (enforces the schema on Claude's output) ─────────────────
# REQ: Part 3.3 — inline JSON schema passed to Claude via tool_use.
# The input_schema IS the UrlResponse schema from specs/url-shortener.yaml.
EMIT_TOOL: anthropic.types.ToolParam = {
    "name": "emit_url_response",
    "description": (
        "Emit a single UrlResponse JSON object representing a newly created short URL. "
        "Call this tool exactly once with a realistic, well-formed UrlResponse. "
        "Never respond with free text."
    ),
    "input_schema": {
        "type": "object",
        "required": ["shortCode", "shortUrl", "longUrl", "createdAt", "isActive"],
        "additionalProperties": False,
        "properties": {
            "shortCode": {
                "type": "string",
                "pattern": "^[A-Za-z0-9-]{3,32}$",
                "description": "8-char alphanumeric code, e.g. 'aB3xY9Kp'",
            },
            "shortUrl": {
                "type": "string",
                "format": "uri",
                "description": "Full short URL, e.g. 'https://sho.rt/aB3xY9Kp'",
            },
            "longUrl": {
                "type": "string",
                "format": "uri",
                "description": "Original long URL, max 2048 chars",
            },
            "alias": {
                "type": ["string", "null"],
                "description": "Custom alias or null",
            },
            "createdAt": {
                "type": "string",
                "format": "date-time",
                "description": "ISO 8601 creation timestamp",
            },
            "expiresAt": {
                "type": ["string", "null"],
                "format": "date-time",
                "description": "Expiry datetime or null",
            },
            "isActive": {
                "type": "boolean",
                "description": "True for active URLs",
            },
        },
    },
}


def call_claude_with_schema(long_url: str, alias: str | None = None) -> dict:
    """
    Call Claude with tool_use to generate a schema-enforced UrlResponse.

    REQ: Part 3.3 — inline JSON schema enforced via tool_use.

    Args:
        long_url: The long URL to "shorten" in the example.
        alias:    Optional custom alias.

    Returns:
        The validated tool_use input dict (the UrlResponse payload).

    Raises:
        ValueError: If Claude doesn't call the tool or output fails schema validation.
    """
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    prompt = (
        f"Generate a realistic UrlResponse for shortening this URL:\n"
        f"  longUrl: {long_url}\n"
        f"  alias:   {alias or 'none (auto-generate a realistic 8-char code)'}\n\n"
        f"Today's date is {datetime.now(timezone.utc).date().isoformat()}. "
        f"Set createdAt to now. Set expiresAt to 30 days from now. "
        f"Call emit_url_response with the result."
    )

    print(f"[schema_runner] Calling Claude (claude-sonnet-4-6)…")
    print(f"[schema_runner] Long URL: {long_url}")
    print(f"[schema_runner] Alias:    {alias or '(auto-generated)'}")

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        tools=[EMIT_TOOL],
        # Force Claude to always call the tool (never respond with text)
        tool_choice={"type": "any"},
        messages=[{"role": "user", "content": prompt}],
    )

    # ── Extract tool_use block ─────────────────────────────────────────────
    tool_use_block = next(
        (block for block in response.content if block.type == "tool_use"),
        None,
    )
    if tool_use_block is None:
        raise ValueError(
            f"Claude did not call emit_url_response. "
            f"Stop reason: {response.stop_reason}. "
            f"Content: {response.content}"
        )

    raw_output: dict = tool_use_block.input
    print(f"\n[schema_runner] Claude raw output:\n{json.dumps(raw_output, indent=2)}")

    # ── Validate against the JSON schema (REQ: Part 3.3) ──────────────────
    print(f"\n[schema_runner] Validating against output_schema.json…")
    try:
        jsonschema.validate(instance=raw_output, schema=URL_RESPONSE_SCHEMA)
        print("[schema_runner] ✓ Schema validation PASSED")
    except jsonschema.ValidationError as exc:
        print(f"[schema_runner] ✗ Schema validation FAILED: {exc.message}")
        raise

    return raw_output


def main() -> None:
    """Entry point: generate a validated UrlResponse and save to output file."""
    long_url = "https://example.com/very/long/marketing/campaign/path?utm_source=email&utm_campaign=summer2026"
    alias = None  # Let Claude generate the short code

    try:
        validated = call_claude_with_schema(long_url=long_url, alias=alias)
    except jsonschema.ValidationError as exc:
        print(f"\n[schema_runner] Validation failed — output NOT saved.\nError: {exc.message}")
        sys.exit(1)

    # ── Annotate and save ──────────────────────────────────────────────────
    output = {
        "_meta": {
            "generated_by": "schema_runner/schema_runner.py",
            "spec_ref": "SPEC-20260524-001",
            "plan_ref": "ARCH-20260524-001 Phase 5",
            "schema_file": "schema_runner/output_schema.json",
            "model": "claude-sonnet-4-6",
            "tool_name": "emit_url_response",
            "validation_status": "PASSED",
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
        "validated_output": validated,
    }

    OUTPUT_FILE.write_text(json.dumps(output, indent=2))
    print(f"\n[schema_runner] Saved to {OUTPUT_FILE}")
    print("[schema_runner] Done ✓")


if __name__ == "__main__":
    main()
