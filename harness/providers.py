"""API authentication for the public model-arena repo.

Keys come from environment variables -- never commit them:
    DEEPSEEK_API_KEY   for deepseek-flash  (https://platform.deepseek.com)
    OPENAI_API_KEY     for gpt-6-luna      (https://platform.openai.com)
    ANTHROPIC_API_KEY  for claude-haiku-5-5 (https://console.anthropic.com)

Muse Spark has no public API in this harness; it runs from an answer bank
(see harness/muse_spark.py), so it needs no key.
"""

import os

ANTHROPIC_VERSION = "2023-06-01"


def headers(kind):
    """Return HTTP headers for a provider kind.

    kind: "deepseek" | "openai" | "anthropic"
    """
    if kind == "deepseek":
        key = os.environ.get("DEEPSEEK_API_KEY")
        if not key:
            raise RuntimeError("DEEPSEEK_API_KEY is not set")
        return {"Content-Type": "application/json",
                "Authorization": "Bearer " + key}
    if kind == "openai":
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise RuntimeError("OPENAI_API_KEY is not set")
        return {"Content-Type": "application/json",
                "Authorization": "Bearer " + key}
    if kind == "anthropic":
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        # Anthropic reads the key from x-api-key, NOT Bearer,
        # and every request needs the anthropic-version header.
        return {"Content-Type": "application/json",
                "x-api-key": key,
                "anthropic-version": ANTHROPIC_VERSION}
    raise ValueError("unknown provider kind: %r" % kind)


def kind_for_host(host):
    if "deepseek" in host:
        return "deepseek"
    if "openai" in host:
        return "openai"
    if "anthropic" in host:
        return "anthropic"
    raise ValueError("unknown host: %r" % host)
