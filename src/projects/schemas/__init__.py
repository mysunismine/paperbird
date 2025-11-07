"""Schema package housing JSON Schema definitions for preset validation."""

from importlib import resources


def load_web_preset_schema() -> dict:
    """Return parsed JSON schema for web presets."""

    with resources.files(__package__).joinpath("web_preset.schema.json").open("r", encoding="utf-8") as handle:
        import json

        return json.load(handle)


__all__ = ["load_web_preset_schema"]
