#!/usr/bin/env python3
"""Prepares Wargus's data for the web build of Stratagus (Stratagus's cmake/web.cmake: -DSTRATAGUS_WEB_DATA).

    web/prepare_data.py ORIGINAL OUTPUT --stratagus STRATAGUS [--work WORK]

ORIGINAL is the installed original game: War2Dat.mpq and Install.mpq for the Battle.net Edition (as sold by GOG), or
the files of a DOS CD. OUTPUT gets the data as the web build preloads it, at / (the engine's default data path is the
current directory): what wartool converts from the original, with this repository's scripts, maps and graphics, as
the install puts them together (CMakeLists.txt). STRATAGUS is the Stratagus source, whose headers wartool's build
needs. WORK keeps wartool's build and its conversion between runs (default: OUTPUT.work).

The data is made smaller for the web:
- the music as Ogg Vorbis: wartool has ffmpeg encode it to .ogg without naming a codec, and an ffmpeg without libvorbis
  then writes FLAC in Ogg, which Stratagus can't play (and is 7 times bigger)
- the campaign briefings and victory speeches as Ogg Vorbis instead of WAV (a seventh of the size), with the scripts
  naming them changed to match
- left out: the original archives (copied for re-extracting from the game's menu), the Smacker videos (the engine plays
  them once converted to Ogg Theora, which the web build doesn't include) and the MIDI music

The conversion is done once (delete WORK/extracted to redo it); the data is redone whenever this script changes. When
it's done, OUTPUT/.stamp is rewritten, which relinks the web build.

Needs cmake, a C++ compiler, libpng and zlib (to build wartool), ffmpeg and oggenc (vorbis-tools).
"""

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

WARGUS = Path(__file__).resolve().parent.parent   # this repository


def run(args, **kwargs):
    subprocess.run([str(a) for a in args], check=True, **kwargs)


def build_wartool(work, stratagus):
    """Wartool, built natively on its own (without the launcher, so that SDL isn't needed)."""
    build = work / "wartool-build"
    if not (build / "CMakeCache.txt").exists():
        run(["cmake", "-S", WARGUS, "-B", build, "-DCMAKE_BUILD_TYPE=Release", "-DCMAKE_POLICY_VERSION_MINIMUM=3.5",
             "-DCMAKE_FIND_FRAMEWORK=LAST", "-DENABLE_VENDORED_LIBS=OFF",
             f"-DSTRATAGUS_INCLUDE_DIR={stratagus / 'gameheaders'}", "-DSTRATAGUS=stratagus"])
    run(["cmake", "--build", build, "--parallel", "--target", "wartool"])
    return build / "wartool"


def convert(wartool, original, work):
    """Wartool's conversion of the original, kept in WORK/extracted."""
    extracted = work / "extracted"
    done = extracted / ".converted"
    if not done.exists():
        print("Converting the original data with wartool", flush=True)
        shutil.rmtree(extracted, ignore_errors=True)
        run([wartool, "-r", original, extracted])   # -r: also the music (the Battle.net Edition has it in Install.mpq)
        done.write_text("done\n")
    return extracted


def to_vorbis(path, out, quality):
    """Encodes any audio ffmpeg reads to Ogg Vorbis, which is what Stratagus plays (SDL_mixer's Ogg support)."""
    decode = subprocess.Popen(["ffmpeg", "-v", "error", "-i", str(path), "-f", "wav", "-"], stdout=subprocess.PIPE)
    run(["oggenc", "-Q", "-q", quality, "-o", out, "-"], stdin=decode.stdout)
    decode.stdout.close()
    if decode.wait():
        sys.exit(f"Couldn't decode {path}")


def codec(path):
    return subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=codec_name",
                           "-of", "csv=p=0", str(path)], capture_output=True, text=True).stdout.strip()


def prepare(extracted, output):
    """The data in OUTPUT, from the conversion and this repository's files."""
    shutil.rmtree(output, ignore_errors=True)
    shutil.copytree(extracted, output, ignore=shutil.ignore_patterns(
        "*.mpq", "*.MPQ", "videos", "data_copied.wargus", ".converted", "*.mid"))
    for folder in ("shaders", "contrib", "campaigns", "maps", "scripts"):
        shutil.copytree(WARGUS / folder, output / folder, dirs_exist_ok=True)
    contrib = WARGUS / "contrib"
    shutil.copy(contrib / "red_cross.png", output / "graphics" / "missiles")
    shutil.copy(contrib / "cross.png", output / "graphics" / "ui" / "cursors")
    for name in ("black_title.png", "food.png", "health.png", "health2.png", "mana.png", "mana2.png",
                 "ore,stone,coal.png", "score.png"):
        shutil.copy(contrib / name, output / "graphics" / "ui")

    # The music, and the campaign speeches, as Ogg Vorbis
    jobs = [(path, path.with_suffix(".ogg"), 4) for path in sorted((output / "music").iterdir())
            if path.suffix in (".wav", ".ogg") and codec(path) != "vorbis"]
    jobs += [(wav, wav.with_suffix(".ogg"), 3) for wav in sorted((output / "campaigns").rglob("*.wav"))]

    def encode(job):
        path, ogg, quality = job
        tmp = ogg.with_name(ogg.name + ".tmp")
        to_vorbis(path, tmp, quality)
        path.unlink()
        tmp.rename(ogg)
    with ThreadPoolExecutor(os.cpu_count()) as pool:
        list(pool.map(encode, jobs))

    # The scripts name the campaign speeches by file: point them to the .ogg ones, and check every one exists
    scripts = [*(output / "campaigns").rglob("*.sms"), *(output / "campaigns").rglob("*.lua"),
               *(output / "scripts").rglob("*.lua")]
    for script in scripts:
        text = script.read_text(errors="surrogateescape")
        changed = re.sub(r'"(campaigns/[^"]+)\.wav"',
                         lambda m: f'"{m[1]}.ogg"' if (output / f"{m[1]}.ogg").exists() else m[0], text)
        if changed != text:
            script.write_text(changed, errors="surrogateescape")
        for name in re.findall(r'"(campaigns/[^"]+\.(?:wav|ogg))"', changed):
            if not (output / name).exists():
                sys.exit(f"{script.relative_to(output)} names {name}, which isn't in the data")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("original", type=Path, help="the installed original game")
    parser.add_argument("output", type=Path, help="where to put the data")
    parser.add_argument("--stratagus", type=Path, required=True, help="the Stratagus source")
    parser.add_argument("--work", type=Path, help="where to keep wartool's build and conversion (default: OUTPUT.work)")
    args = parser.parse_args()
    missing = [n for n in ("cmake", "ffmpeg", "ffprobe", "oggenc") if not shutil.which(n)]
    if missing:
        sys.exit(f"Not found: {', '.join(missing)}")
    output = args.output.resolve()
    work = (args.work or output.with_name(output.name + ".work")).resolve()
    work.mkdir(parents=True, exist_ok=True)

    extracted = convert(build_wartool(work, args.stratagus.resolve()), args.original.resolve(), work)
    # Redone whenever this script, or the conversion, changes
    version = hashlib.sha256(Path(__file__).read_bytes() + (extracted / ".converted").read_bytes()).hexdigest()
    stamp = output / ".stamp"
    if stamp.exists() and stamp.read_text() == version:
        print(f"{output} is up to date")
        return
    print(f"Preparing the data in {output}", flush=True)
    prepare(extracted, output)
    stamp.write_text(version)


if __name__ == "__main__":
    main()
