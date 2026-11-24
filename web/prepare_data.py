#!/usr/bin/env python3
"""Prepares Wargus's data for the web build of Stratagus (Stratagus's cmake/web.cmake: -DSTRATAGUS_WEB_DATA). Run by
web/CMakeLists.txt, which builds the two together.

    web/prepare_data.py ORIGINAL OUTPUT --stratagus STRATAGUS [--work WORK] [--icon ICON] [--localized LANG=INSTALL]...

ORIGINAL is the original game: the installed game (War2Dat.mpq and Install.mpq for the Battle.net Edition, or the files
of a DOS CD), or GOG's installer (its .exe, with its .bin next to it, named after it: setup_warcraft_ii.exe and
setup_warcraft_ii-1.bin, say: innoextract looks for <name>-1.bin), which is unpacked into WORK/original the first time;
after that, it isn't needed. OUTPUT gets the data as the web build preloads it, at / (the engine's default data path is
the current directory): what wartool converts from the original, with this repository's scripts, maps and graphics, as
the install puts them together (CMakeLists.txt). STRATAGUS is the Stratagus source, whose headers wartool's build
needs. WORK keeps wartool's build, the unpacked installer and the conversion between runs (default: OUTPUT.work). ICON,
if given, gets a 256x256 PNG icon: the game's own from GOG's installer when the original has it, otherwise this
repository's.

The game in other languages: --localized LANG=INSTALL gives the INSTALL.EXE of a Battle.net Edition CD in another
language (LANG as Wargus's translations name it: it for scripts/translate/it_IT.po). That's where the CD has the game's
texts, speech and music (in War2Dat.mpq and Install.mpq, inside it); the rest of the CD, the graphics, is the same in
every language, and wartool takes it from the main original (its Support/TOMES). The conversion is kept in
WORK/localized/LANG/extracted, and used from then on: after that INSTALL isn't needed (without either, the game is
built without that language). What it has that differs from the main conversion (the campaigns' texts and speech, the
units' voices) goes into OUTPUT/LANG, where the engine looks first when the game is in that language
(SetLanguageDataPath, in scripts/stratagus.lua). The rest is the same in every language.

The data is made smaller for the web:
- the music as Ogg Vorbis: wartool has ffmpeg encode it to .ogg without naming a codec, and an ffmpeg without libvorbis
  then writes FLAC in Ogg, which Stratagus can't play (and is 7 times bigger)
- the campaign briefings and victory speeches as Ogg Vorbis instead of WAV (a seventh of the size), with the scripts
  naming them changed to match
- left out: the original archives (copied for re-extracting from the game's menu), the Smacker videos (the engine plays
  them once converted to Ogg Theora, which the web build doesn't include) and the MIDI music

The conversion is done once (delete WORK/extracted to redo it); the data is redone whenever this script, or this
repository's files it includes, change. When
it's done, OUTPUT/.stamp is rewritten, which relinks the web build.

Needs cmake, a C++ compiler, libpng and zlib (to build wartool), ffmpeg and oggenc (vorbis-tools), innoextract (for an
installer), and Pillow (for the icon).
"""

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import zlib
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


def unpack(original, work):
    """The installed game: ORIGINAL itself, or GOG's installer unpacked into WORK/original (once)."""
    unpacked = work / "original"
    if (unpacked / ".unpacked").exists():
        return unpacked
    if original is None:
        sys.exit("Give the original game: the installed game, or GOG's installer")
    if original.is_dir():
        return original
    if not original.is_file():
        sys.exit(f"The original game isn't at {original}: give the installed game, or GOG's installer")
    if not shutil.which("innoextract"):
        sys.exit("innoextract not found: it unpacks GOG's installer")
    print(f"Unpacking {original.name}", flush=True)
    shutil.rmtree(unpacked, ignore_errors=True)
    run(["innoextract", "-s", "-d", unpacked, original])
    (unpacked / ".unpacked").write_text(f"{original.name}\n")
    return unpacked


def write_icon(original, path):
    """A 256x256 icon: GOG's, when the original has it (its round portrait scaled to 82% of the icon, as round icons
    usually are, so that it doesn't look bigger than others next to it), otherwise this repository's."""
    from PIL import Image
    icons = sorted((original / "app").glob("goggame-*.ico"))
    if icons:
        icon = Image.open(icons[0])
        icon.size = max(icon.info["sizes"])
        icon = icon.convert("RGBA")
        icon = icon.crop(icon.getchannel("A").point(lambda a: 255 if a > 32 else 0).getbbox())
        side = round(256 * 0.82)
    else:
        icon = Image.open(WARGUS / "wargus.png").convert("RGBA")
        side = 256
    icon = icon.resize((side, round(side * icon.height / icon.width)), Image.LANCZOS)
    padded = Image.new("RGBA", (256, 256))
    padded.paste(icon, ((256 - icon.width) // 2, (256 - icon.height) // 2))
    padded.save(path)


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


def convert_localized(wartool, original, install, work):
    """Wartool's conversion of the game in another language, from its CD's INSTALL.EXE and the main original's
    tomes, kept in WORK/extracted; None if there's neither the conversion nor INSTALL.EXE."""
    extracted = work / "extracted"
    if (extracted / ".converted").exists():
        return extracted
    if not install.is_file():
        print(f"{install} isn't there: the game is built without that language", flush=True)
        return None
    tomes = next((t for t in (original / "Support" / "TOMES", original / "support" / "tomes") if t.is_dir()), None)
    if not tomes:
        sys.exit(f"The original game has no Support/TOMES (it isn't the Battle.net Edition): can't convert {install}")
    # A CD as wartool reads it: INSTALL.EXE, and the tomes
    cd = work / "cd"
    shutil.rmtree(cd, ignore_errors=True)
    (cd / "Support").mkdir(parents=True)
    (cd / "INSTALL.EXE").symlink_to(install.resolve())
    (cd / "Support" / "TOMES").symlink_to(tomes.resolve())
    convert(wartool, cd, work)
    shutil.rmtree(cd)
    for name in ("install.exe", "INSTALL.EXE"):   # INSTALL.EXE, copied by wartool: not needed
        (extracted / name).unlink(missing_ok=True)
    return extracted


def to_vorbis(path, out, quality, serial):
    """Encodes any audio ffmpeg reads to Ogg Vorbis, which is what Stratagus plays (SDL_mixer's Ogg support). The
    stream's serial number is given: oggenc otherwise picks a random one, and the data would differ on every build."""
    decode = subprocess.Popen(["ffmpeg", "-v", "error", "-i", str(path), "-f", "wav", "-"], stdout=subprocess.PIPE)
    run(["oggenc", "-Q", "-q", quality, "-s", serial, "-o", out, "-"], stdin=decode.stdout)
    decode.stdout.close()
    if decode.wait():
        sys.exit(f"Couldn't decode {path}")


def codec(path):
    return subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=codec_name",
                           "-of", "csv=p=0", str(path)], capture_output=True, text=True).stdout.strip()


COPIED = ("shaders", "contrib", "campaigns", "maps", "scripts")   # this repository's folders the data includes


def version(extracted, localized):
    """What the data is made from: this script, the conversions, and the files copied from this repository (by name,
    size and modification time). The data is redone when it changes."""
    h = hashlib.sha256(Path(__file__).read_bytes() + (extracted / ".converted").read_bytes())
    for language, converted in localized.items():
        h.update(f"{language}\0".encode() + (converted / ".converted").read_bytes())
    for folder in COPIED:
        for path in sorted((WARGUS / folder).rglob("*")):
            if path.is_file():
                st = path.stat()
                h.update(f"{path.relative_to(WARGUS)}\0{st.st_size}\0{st.st_mtime_ns}\n".encode())
    return h.hexdigest()


LEFT_OUT = ("*.mpq", "*.MPQ", "videos", "data_copied.wargus", ".converted", "*.mid")
# What the game in another language has of its own: everything that differs, except what isn't in the data, the music
# (the same, but encoded differently each time), the archives copied for re-extracting, and the scripts (the only one
# wartool writes has its texts, which aren't used, and the settings, which are the same)
LOCALIZED_LEFT_OUT = ("music", "support", "scripts", "extracted", "*.exe", "*.EXE")


def differing(extracted, other):
    """The files of the conversion OTHER that aren't in the conversion EXTRACTED as they are, relative to OTHER."""
    left_out = shutil.ignore_patterns(*LEFT_OUT)
    top_left_out = shutil.ignore_patterns(*LOCALIZED_LEFT_OUT)
    found = []
    def walk(folder, top):
        names = sorted(p.name for p in folder.iterdir())
        ignored = left_out(str(folder), names) | (top_left_out(str(folder), names) if top else set())
        for name in names:
            path = folder / name
            if name in ignored:
                continue
            if path.is_dir():
                walk(path, False)
            else:
                relative = path.relative_to(other)
                base = extracted / relative
                if not base.is_file() or base.read_bytes() != path.read_bytes():
                    found.append(relative)
    walk(other, True)
    return found


def prepare(extracted, localized, output):
    """The data in OUTPUT, from the conversions and this repository's files."""
    shutil.rmtree(output, ignore_errors=True)
    shutil.copytree(extracted, output, ignore=shutil.ignore_patterns(*LEFT_OUT))
    for folder in COPIED:
        shutil.copytree(WARGUS / folder, output / folder, dirs_exist_ok=True)
    contrib = WARGUS / "contrib"
    shutil.copy(contrib / "red_cross.png", output / "graphics" / "missiles")
    shutil.copy(contrib / "cross.png", output / "graphics" / "ui" / "cursors")
    for name in ("black_title.png", "food.png", "health.png", "health2.png", "mana.png", "mana2.png",
                 "ore,stone,coal.png", "score.png"):
        shutil.copy(contrib / name, output / "graphics" / "ui")

    # The game in other languages: what it has of its own, in a folder named after the language
    for language, converted in localized.items():
        files = differing(extracted, converted)
        print(f"{language}: {len(files)} files of its own", flush=True)
        for relative in files:
            (output / language / relative).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(converted / relative, output / language / relative)
    roots = [output, *(output / language for language in localized)]   # the data, and its languages'

    # The music, and the campaign speeches, as Ogg Vorbis
    jobs = [(path, path.with_suffix(".ogg"), 4) for path in sorted((output / "music").iterdir())
            if path.suffix in (".wav", ".ogg") and codec(path) != "vorbis"]
    jobs += [(wav, wav.with_suffix(".ogg"), 3) for root in roots for wav in sorted((root / "campaigns").rglob("*.wav"))]

    def encode(job):
        path, ogg, quality = job
        tmp = ogg.with_name(ogg.name + ".tmp")
        # the same serial for the same file on every build: from its name in the data
        serial = zlib.crc32(ogg.relative_to(output).as_posix().encode()) & 0x7FFFFFFF
        to_vorbis(path, tmp, quality, serial)
        path.unlink()
        tmp.rename(ogg)
    with ThreadPoolExecutor(os.cpu_count()) as pool:
        list(pool.map(encode, jobs))

    # The scripts name the campaign speeches by file: point them to the .ogg ones, and check every one exists (in a
    # language's folder, a script can name the language's speech or the data's)
    for root in roots:
        scripts = [*(root / "campaigns").rglob("*.sms"), *(root / "campaigns").rglob("*.lua"),
                   *(root / "scripts").rglob("*.lua")]
        def exists(name):
            return (root / name).exists() or (output / name).exists()
        for script in scripts:
            text = script.read_text(errors="surrogateescape")
            changed = re.sub(r'"(campaigns/[^"]+)\.wav"',
                             lambda m: f'"{m[1]}.ogg"' if exists(f"{m[1]}.ogg") else m[0], text)
            if changed != text:
                script.write_text(changed, errors="surrogateescape")
            for name in re.findall(r'"(campaigns/[^"]+\.(?:wav|ogg))"', changed):
                if not exists(name):
                    sys.exit(f"{script.relative_to(output)} names {name}, which isn't in the data")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("original", type=Path, help="the installed original game, or GOG's installer")
    parser.add_argument("output", type=Path, help="where to put the data")
    parser.add_argument("--stratagus", type=Path, required=True, help="the Stratagus source")
    parser.add_argument("--work", type=Path, help="where to keep wartool's build, the unpacked installer and the "
                        "conversion (default: OUTPUT.work)")
    parser.add_argument("--icon", type=Path, help="where to write a 256x256 PNG icon")
    parser.add_argument("--localized", action="append", default=[], metavar="LANG=INSTALL",
                        help="the INSTALL.EXE of a Battle.net Edition CD in another language")
    args = parser.parse_args()
    missing = [n for n in ("cmake", "ffmpeg", "ffprobe", "oggenc") if not shutil.which(n)]
    if missing:
        sys.exit(f"Not found: {', '.join(missing)}")
    output = args.output.resolve()
    work = (args.work or output.with_name(output.name + ".work")).resolve()
    work.mkdir(parents=True, exist_ok=True)

    # An empty ORIGINAL (the installer was removed after it was unpacked) is fine once it's unpacked
    original = unpack(args.original.resolve() if args.original.parts else None, work)
    if args.icon:
        write_icon(original, args.icon)
    wartool = build_wartool(work, args.stratagus.resolve())
    extracted = convert(wartool, original, work)
    localized = {}
    for entry in args.localized:
        language, _, install = entry.partition("=")
        if not re.fullmatch(r"[a-z]+", language) or not install:
            sys.exit(f"--localized {entry}: give LANG=INSTALL, such as it=install_it.exe")
        converted = convert_localized(wartool, original, Path(install), work / "localized" / language)
        if converted:
            localized[language] = converted
    current = version(extracted, localized)
    stamp = output / ".stamp"
    if stamp.exists() and stamp.read_text() == current:
        print(f"{output} is up to date")
        return
    print(f"Preparing the data in {output}", flush=True)
    prepare(extracted, localized, output)
    stamp.write_text(current)


if __name__ == "__main__":
    main()
