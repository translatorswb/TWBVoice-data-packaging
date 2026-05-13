"""Render the dataset card from a Jinja template."""

from __future__ import annotations

from pathlib import Path

import jinja2


TEMPLATES_DIR = Path(__file__).resolve().parent.parent.parent / "templates"


def build_frontmatter(
    *,
    language_code: str,
    card,
    all_tags: list[str],
    download_size_bytes: int | None = None,
    dataset_size_bytes: int | None = None,
) -> str:
    """Build the HuggingFace-style YAML frontmatter as a plain string.

    Doing this in Python (rather than Jinja) keeps the YAML indentation
    predictable — Jinja's whitespace controls fight with multi-line YAML.
    """
    lines: list[str] = ["---"]
    lines.append("language:")
    lines.append(f"  - {language_code}")
    if card.license_id:
        lines.append(f"license: {card.license_id}")
    if card.task_categories:
        lines.append("task_categories:")
        for t in card.task_categories:
            lines.append(f"  - {t}")
    if card.size_categories:
        lines.append("size_categories:")
        lines.append(f"  - {card.size_categories}")
    if all_tags:
        lines.append("tags:")
        for t in all_tags:
            lines.append(f"  - {t}")
    if card.pretty_name:
        lines.append(f'pretty_name: "{card.pretty_name}"')
    if download_size_bytes is not None:
        lines.append(f"download_size: {download_size_bytes}")
    if dataset_size_bytes is not None:
        lines.append(f"dataset_size: {dataset_size_bytes}")
    lines.append("---")
    return "\n".join(lines)


def render_card(context: dict, template_name: str = "dataset_card.md.j2") -> str:
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=False,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    return env.get_template(template_name).render(**context)
