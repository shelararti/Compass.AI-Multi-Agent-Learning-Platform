"""
review_analysis.py — human review/edit pass over a cached lecture analysis.

render_video.py saves its full per-chunk analysis (transcription slice,
classification, extracted content) to output/<video>_analysis.json before
rendering. This script walks through that file chunk by chunk so you can
catch and fix a wrong classification or a bad extraction BEFORE spending
render time on it — no Whisper or Ollama involved, just editing JSON.

For each chunk:
    Enter    accept as-is, move to next chunk
    d        change the visual_domain (prompts for a valid value)
    i        mark this chunk as "ignore" (renderer will skip it)
    e        open this chunk's extracted "content" in $EDITOR to hand-fix it
    q        save everything and quit
    Ctrl-C   quit WITHOUT saving anything

Usage:
    python review_analysis.py lecture.mp4          # resolves to output/lecture_analysis.json
    python review_analysis.py output/lecture_analysis.json

After editing, just re-run render_video.py on the same video — it will pick
up your changes from the cache instead of re-transcribing.
"""

import json
import os
import pathlib
import subprocess
import sys
import tempfile

DOMAINS = ["conceptual", "data", "narrative", "process", "scene", "spatial", "formula", "ignore"]


def resolve_path(arg: str) -> pathlib.Path:
    p = pathlib.Path(arg)
    if p.suffix == ".json":
        return p
    # Treat it as a video path and derive the analysis file render_video.py
    # would have written for it.
    return pathlib.Path("output") / f"{p.stem}_analysis.json"


def load(path: pathlib.Path) -> dict:
    if not path.exists():
        print(f"No analysis file found at '{path}'.")
        print("Run render_video.py on the video first — that's what generates it.")
        sys.exit(1)
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as e:
        print(f"'{path}' isn't valid JSON ({e}). Fix it by hand or delete it and "
              f"re-run render_video.py --rebuild to regenerate it.")
        sys.exit(1)


def save(path: pathlib.Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2))
    print(f"\nSaved changes to '{path}'.")


def edit_in_editor(obj) -> dict:
    """Open a chunk's content dict in $EDITOR as JSON; fall back to the
    original unchanged if the result doesn't parse, rather than losing it."""
    editor = os.environ.get("EDITOR", "nano")
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(obj, f, indent=2)
        tmp_path = f.name

    try:
        subprocess.run([editor, tmp_path])
        edited = pathlib.Path(tmp_path).read_text()
        return json.loads(edited)
    except json.JSONDecodeError as e:
        print(f"  [edit] invalid JSON after editing ({e}) — keeping content unchanged")
        return obj
    except FileNotFoundError:
        print(f"  [edit] editor '{editor}' not found — set $EDITOR to something installed "
              f"(e.g. 'export EDITOR=nano'). Keeping content unchanged.")
        return obj
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def format_time(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m:02d}:{s:02d}"


def main():
    if len(sys.argv) < 2:
        print("Usage: python review_analysis.py <video.mp4 | analysis.json>")
        sys.exit(1)

    path = resolve_path(sys.argv[1])
    data = load(path)
    chunks = data.get("chunks", [])
    if not chunks:
        print(f"'{path}' has no chunks to review.")
        sys.exit(0)

    print(f"Reviewing {len(chunks)} chunks from '{path}'")
    print("(Enter=accept · d=change domain · i=mark ignore · e=edit content · q=save & quit)\n")

    try:
        for i, chunk in enumerate(chunks):
            domain = chunk.get("visual_domain", "ignore")
            action = chunk.get("scene_action", "IGNORE")
            confidence = chunk.get("confidence", 0.0)
            text = chunk.get("input_text", "")
            keywords = chunk.get("keywords", [])
            content = chunk.get("content", {})

            print("-" * 64)
            print(f"[{i+1}/{len(chunks)}] {format_time(chunk['start'])}-{format_time(chunk['end'])}  "
                  f"domain={domain}  action={action}  confidence={confidence}")
            print(f"  transcript: {text[:100]}{'...' if len(text) > 100 else ''}")
            if keywords:
                print(f"  keywords: {', '.join(str(k) for k in keywords)}")
            if content:
                print(f"  content: {json.dumps(content)[:200]}")

            while True:
                choice = input("  > ").strip().lower()

                if choice == "":
                    break

                elif choice == "d":
                    print(f"  domains: {', '.join(DOMAINS)}")
                    new_domain = input("  new domain: ").strip().lower()
                    if new_domain in DOMAINS:
                        chunk["visual_domain"] = new_domain
                        if new_domain == "ignore":
                            chunk["scene_action"] = "IGNORE"
                        print(f"  -> domain set to '{new_domain}'")
                    else:
                        print(f"  '{new_domain}' isn't a recognized domain — no change made")
                    continue

                elif choice == "i":
                    chunk["visual_domain"] = "ignore"
                    chunk["scene_action"] = "IGNORE"
                    print("  -> marked as ignore")
                    break

                elif choice == "e":
                    chunk["content"] = edit_in_editor(content)
                    content = chunk["content"]
                    print(f"  -> content is now: {json.dumps(content)[:200]}")
                    continue

                elif choice == "q":
                    save(path, data)
                    return

                else:
                    print("  unrecognized option (Enter/d/i/e/q)")

        save(path, data)

    except KeyboardInterrupt:
        print("\n\nQuit without saving — no changes were written.")
        sys.exit(1)


if __name__ == "__main__":
    main()
