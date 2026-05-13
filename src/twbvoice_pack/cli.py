"""Top-level CLI for twbvoice-pack."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import analyze, card, config as cfg_mod, enrich, healthcheck, policies, tarball


def _print(label: str, ok: bool, msg: str = "") -> None:
    mark = "✓" if ok else "⚠"
    print(f"  {mark} {label}" + (f": {msg}" if msg else ""))


def _auto_size_categories(total_rows: int) -> str:
    bounds = [
        (1_000, "n<1K"),
        (10_000, "1K<n<10K"),
        (100_000, "10K<n<100K"),
        (1_000_000, "100K<n<1M"),
        (10_000_000, "1M<n<10M"),
    ]
    for limit, label in bounds:
        if total_rows < limit:
            return label
    return "n>10M"


def cmd_run(args: argparse.Namespace) -> int:
    # Resolve --out-dir against CWD so it doesn't get reinterpreted relative
    # to the config file's directory.
    out_dir = args.out_dir.resolve() if args.out_dir else None
    cfg = cfg_mod.load(args.config, output_dir=out_dir)

    print(f"Delivery: {cfg.delivery.language.name} ({cfg.delivery.language.code}) — {cfg.delivery.date}")
    print(f"Flows:    {', '.join(cfg.flows.keys())}")
    print(f"Output:   {cfg.output_dir}")
    print()

    summary_lines: list[str] = [
        f"# {cfg.delivery.language.name} — {cfg.delivery.date}",
        "",
    ]
    flow_ctx: dict[str, dict] = {}
    records_by_flow: dict[str, tuple] = {}

    for flow_name, flow in cfg.flows.items():
        print(f"=== flow: {flow_name} ===")
        print(f"  record_zip:          {flow.record_zip.name}")
        print(f"  recording_check:     {flow.recording_check.name if flow.recording_check else '(none)'}")
        print(f"  transcription_check: {flow.transcription_check.name if flow.transcription_check else '(none)'}")
        print(f"  filename path:       content.{flow.record_filename_path}.filename")
        print(f"  transcription field: {'yes' if flow.has_transcription_field else 'no'}")

        records, enrich_stats = enrich.enrich_flow(flow)
        _print(
            f"enriched {enrich_stats['output_rows']} rows "
            f"(dropped {len(enrich_stats['pii_dropped'])} PII)",
            ok=True,
        )

        records, policy_stats = policies.apply(records, flow, cfg.policies)
        _print(
            f"policies: dropped {len(policy_stats['dropped_offensive'])} offensive, "
            f"{len(policy_stats['dropped_pending'])} pending, "
            f"{len(policy_stats['dropped_rejected'])} rejected",
            ok=True,
        )

        health = healthcheck.check_flow(records, flow)
        for line in healthcheck.format_report(health):
            print(line)

        stats = analyze.analyze_flow(records, flow)
        _print(
            f"analyzed: {stats['rows']} rows, "
            f"{stats['distinct_speakers_approved']} approved speakers, "
            f"{stats['hours_by_status'].get('approved', 0):.2f} approved hours",
            ok=True,
        )

        records_by_flow[flow_name] = (flow, records)
        flow_ctx[flow_name] = {
            "spec": flow,
            "stats": stats,
            "health": health,
            "pii_dropped": enrich_stats["pii_dropped"],
            "dropped_offensive": policy_stats["dropped_offensive"],
            "dropped_pending": policy_stats["dropped_pending"],
            "dropped_rejected": policy_stats["dropped_rejected"],
        }

        summary_lines.append(f"## {flow_name}")
        summary_lines.append(
            f"- rows: {stats['rows']}, files: {stats['distinct_files']}, "
            f"approved speakers: {stats['distinct_speakers_approved']}, "
            f"approved hours: {stats['hours_by_status'].get('approved', 0):.2f}"
        )
        summary_lines.append(
            f"- approved/pending/rejected: "
            f"{stats['approval'].get('approved', 0)}/"
            f"{stats['approval'].get('pending', 0)}/"
            f"{stats['approval'].get('rejected', 0)}"
        )
        summary_lines.append(
            f"- dropped: PII {len(enrich_stats['pii_dropped'])}, "
            f"offensive {len(policy_stats['dropped_offensive'])}, "
            f"pending {len(policy_stats['dropped_pending'])}, "
            f"rejected {len(policy_stats['dropped_rejected'])}"
        )
        summary_lines.append("")
        print()

    # ---- Card context ----
    total_rows = sum(ctx["stats"]["rows"] for ctx in flow_ctx.values())
    size_categories = cfg.card.size_categories or _auto_size_categories(total_rows)

    # Tags: language code + user-supplied
    all_tags: list[str] = []
    for t in [cfg.delivery.language.code, *cfg.card.tags]:
        if t and t not in all_tags:
            all_tags.append(t)

    # Build a card-spec view that also carries the auto-derived size_categories.
    class _Card:
        def __init__(self, src, size_categories_override):
            for k, v in src.__dict__.items():
                setattr(self, k, v)
            self.size_categories = size_categories_override

    card_view = _Card(cfg.card, size_categories)

    def _render(download_size_bytes=None, dataset_size_bytes=None) -> str:
        fm = card.build_frontmatter(
            language_code=cfg.delivery.language.code,
            card=card_view,
            all_tags=all_tags,
            download_size_bytes=download_size_bytes,
            dataset_size_bytes=dataset_size_bytes,
        )
        return card.render_card({
            "frontmatter": fm,
            "delivery": cfg.delivery,
            "card": card_view,
            "policies": cfg.policies,
            "flows": flow_ctx,
        })

    # First render — without size info — goes into the tarball.
    card_md = _render()

    # ---- Tarball ----
    tar_path, stamped_records, audio_bytes = tarball.build(cfg, records_by_flow, card_md)
    download_size = tar_path.stat().st_size
    dataset_size = audio_bytes + len(card_md.encode("utf-8"))
    _print(
        f"built {tar_path.name} "
        f"(tarball {download_size/1e9:.2f} GB; uncompressed audio {audio_bytes/1e9:.2f} GB; "
        f"{len(stamped_records)} rows)",
        ok=True,
    )

    # Re-render with final sizes for the loose sibling card.md
    final_card = _render(
        download_size_bytes=download_size,
        dataset_size_bytes=dataset_size,
    )
    card_path = cfg.output_dir / f"{cfg.slug}_dataset_card.md"
    card_path.write_text(final_card)

    summary_path = cfg.output_dir / f"{cfg.slug}_summary.md"
    summary_path.write_text("\n".join(summary_lines))

    print()
    print(f"Tarball:  {tar_path}")
    print(f"Card:     {card_path}")
    print(f"Summary:  {summary_path}")
    print("\nREADY TO SHIP.")
    return 0


def cmd_push_hf(args: argparse.Namespace) -> int:
    from . import hf_upload
    return hf_upload.push(args.repo_id, args.out_dir, private=not args.public, dry_run=args.dry_run) or 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="twbvoice-pack", description="TWB Voice data packaging.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="Build a delivery from a YAML config.")
    run.add_argument("config", type=Path, help="Path to delivery YAML.")
    run.add_argument("--out-dir", type=Path, default=None, help="Output dir (default: <config-dir>/out).")
    run.set_defaults(func=cmd_run)

    pushhf = sub.add_parser("push-hf", help="Push a built delivery to a HF dataset repo.")
    pushhf.add_argument("--repo-id", required=True)
    pushhf.add_argument("--out-dir", type=Path, required=True)
    pushhf.add_argument("--public", action="store_true")
    pushhf.add_argument("--dry-run", action="store_true")
    pushhf.set_defaults(func=cmd_push_hf)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
