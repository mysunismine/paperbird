"""Normalize deprecated OpenAI model names."""

from __future__ import annotations

from django.db import migrations


ALIASES = {
    "gpt-5": "gpt-5.2",
    "gpt-5.0": "gpt-5.2",
    "gpt-5o": "gpt-4o",
    "gpt-5o-mini": "gpt-4o-mini",
}


def _normalize_openai_models(apps, schema_editor) -> None:
    Project = apps.get_model("projects", "Project")
    for legacy, updated in ALIASES.items():
        Project.objects.filter(rewrite_model=legacy).update(rewrite_model=updated)
        Project.objects.filter(image_prompt_model=legacy).update(image_prompt_model=updated)


class Migration(migrations.Migration):
    dependencies = [
        ("projects", "0021_alter_project_image_model_and_more"),
    ]

    operations = [
        migrations.RunPython(_normalize_openai_models, migrations.RunPython.noop),
    ]
