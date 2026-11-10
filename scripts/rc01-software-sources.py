#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Resolve latest application sources once, or replay a build's immutable lock.

Only software feeds and application recipes are writable. Platform, driver,
wireless firmware and bootloader inputs are never selected by this resolver.
Unknown build-recipe changes fail closed instead of dropping local guards.
"""
from pathlib import Path, PurePosixPath
import argparse
import base64
import datetime
import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".rc01-sources"
BASELINES = ROOT / "config/rc01/software-baselines"
HEX40 = re.compile(r"[0-9a-f]{40}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
SEMVER = re.compile(r"v?([0-9]+\.[0-9]+\.[0-9]+)\Z")
OLD_TIME = 'PKG_BUILD_TIME:=$(shell date -u -Iseconds)'
NEW_TIME = 'PKG_BUILD_TIME:=$(shell date -u -d "@$(SOURCE_DATE_EPOCH)" -Iseconds)'
PROTECTED = ("scripts/rc01-platform-sources.json", "target/linux/generic/kernel-6.18",
             "package/kernel/mac80211/Makefile", "package/firmware/ath12k-firmware/Makefile")
PLATFORM_PATHS = (*PROTECTED, "target/linux/qualcommbe", "target/linux/generic/backport-6.18",
                  "target/linux/generic/pending-6.18", "target/linux/generic/hack-6.18",
                  "target/linux/generic/config-6.18", "package/kernel/mac80211",
                  "package/firmware/ath12k-firmware", "package/firmware/ipq-wifi",
                  "package/boot/uboot-tools", "package/network/config/netifd",
                  "package/emortal/luci-app-router-tool", "defconfig/qualcommbe-rc01.config",
                  "config/rc01/files", "package/emortal/rc01-platform")


class SourceError(ValueError):
    pass


def digest(data):
    return hashlib.sha256(data).hexdigest()


def run(*args, cwd=None):
    return subprocess.check_output(list(args), cwd=cwd, text=True).strip()


def api(path):
    return json.loads(run("gh", "api", path))


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n")
    temporary.replace(path)


def write_preparation_phase(lock, state):
    if state not in ("planned", "locked-pending", "complete"):
        raise SourceError("invalid local source preparation phase")
    checksum = digest(json.dumps(lock, sort_keys=True, separators=(",", ":")).encode())
    write_json(STATE / "preparation.json", {"schema": 1, "state": state,
               "mode": lock["mode"], "lock_sha256": checksum})


def preparation_phase(lock):
    path = STATE / "preparation.json"
    if not path.exists() and not path.is_symlink():
        return None
    if path.is_symlink() or not path.is_file():
        raise SourceError("unsafe local source preparation phase")
    phase = json.loads(path.read_text())
    checksum = digest(json.dumps(lock, sort_keys=True, separators=(",", ":")).encode())
    if (phase.get("schema") != 1 or phase.get("state") not in ("planned", "locked-pending", "complete")
            or phase.get("mode") != lock["mode"] or phase.get("lock_sha256") != checksum):
        raise SourceError("local preparation phase differs from the current source lock")
    return phase["state"]


def platform_inputs():
    # Bind the actual reviewed platform implementation, not just its versions.
    # Git lists public source inputs and excludes ignored prepared/BDF caches.
    if (ROOT / ".git").exists():
        names = run("git", "ls-files", "--cached", "--others", "--exclude-standard", "--", *PLATFORM_PATHS,
                    cwd=ROOT).splitlines()
    else:
        # Temporary source fixtures need no repository or network access.
        names = []
        for name in PLATFORM_PATHS:
            path = ROOT / name
            if path.is_file():
                names.append(name)
            elif path.is_dir():
                names.extend(str(p.relative_to(ROOT)) for p in path.rglob("*") if p.is_file())
    return {name: digest((ROOT / name).read_bytes()) for name in sorted(set(names)) if (ROOT / name).is_file()}


def github_repo(url):
    match = re.fullmatch(r"https://github.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?", url)
    if not match:
        raise SourceError(f"unsupported feed URL: {url}")
    return match[1]


def stable_release(repo, get=api):
    # /latest excludes drafts/prereleases server-side; validate again locally.
    release = get(f"repos/{repo}/releases/latest")
    if release.get("draft") is not False or release.get("prerelease") is not False:
        raise SourceError(f"{repo}: latest response is not a stable published release")
    tag = release.get("tag_name", "")
    if not SEMVER.fullmatch(tag):
        raise SourceError(f"{repo}: unsupported stable release tag {tag!r}")
    commit = get(f"repos/{repo}/commits/{urllib.parse.quote(tag, safe='')}")
    if not HEX40.fullmatch(commit.get("sha", "")):
        raise SourceError(f"{repo}: missing immutable release commit")
    return {"repository": repo, "tag": tag, "version": SEMVER.fullmatch(tag)[1],
            "commit": commit["sha"], "published_at": release.get("published_at"),
            "release_url": release.get("html_url"),
            "epoch": int(datetime.datetime.fromisoformat(commit["commit"]["committer"]["date"].replace("Z", "+00:00")).timestamp())}


def feed_plan(mode, get=api):
    result = {}
    for line in (ROOT / "config/rc01/feeds.conf").read_text().splitlines():
        if not line.startswith("src-git "):
            continue
        _, name, pinned = line.split()
        url, commit = pinned.rsplit("^", 1)
        repo = github_repo(url)
        branch = None
        if mode == "latest":
            branch = get(f"repos/{repo}")["default_branch"]
            commit = get(f"repos/{repo}/commits/{urllib.parse.quote(branch, safe='')}")["sha"]
        if not HEX40.fullmatch(commit):
            raise SourceError(f"invalid feed commit: {name}")
        result[name] = {"url": url, "branch": branch, "commit": commit}
    if set(result) != {"packages", "luci", "routing", "nikki", "daede"}:
        raise SourceError("unexpected software feed set")
    return result


def feeds_text(lock):
    return "".join(f"src-git {name} {item['url']}^{item['commit']}\n" for name, item in lock["feeds"].items())


def validate_lock(lock):
    if lock.get("mode") not in ("latest", "locked"):
        raise SourceError("invalid software lock mode")
    if lock.get("schema") != 1 or lock.get("platform_inputs") != platform_inputs():
        raise SourceError("software lock belongs to different reviewed platform inputs")
    if (type(lock.get("source_date_epoch")) is not int or lock["source_date_epoch"] < 0
            or type(lock.get("with_dae")) is not bool or not HEX40.fullmatch(lock.get("build_commit", ""))):
        raise SourceError("invalid build epoch, variant or source commit in software lock")
    allowed = feed_plan("locked")
    if set(lock.get("feeds", {})) != set(allowed):
        raise SourceError("unexpected software source-lock feed set")
    for name, item in lock["feeds"].items():
        if item.get("url") != allowed[name]["url"] or not HEX40.fullmatch(item.get("commit", "")):
            raise SourceError(f"unexpected software source-lock origin/commit: {name}")
        if lock["mode"] == "locked" and item["commit"] != allowed[name]["commit"]:
            raise SourceError(f"locked feed differs from reviewed baseline: {name}")
    if lock["mode"] == "latest" and lock.get("package_manifest"):
        entries = lock["package_manifest"]["packages"]
        expected = {("packages", "net/tailscale/Makefile"): ("tailscale/tailscale", "tailscale"),
                    ("nikki", "mihomo-meta/Makefile"): ("MetaCubeX/mihomo", "mihomo-meta")}
        if {(e["feed"], e["path"]) for e in entries} != set(expected) or len(entries) != 2:
            raise SourceError("unexpected resolved application set")
        for entry in entries:
            repo, package = expected[(entry["feed"], entry["path"])]
            release = entry["release"]
            version = entry["version"]
            if (release.get("repository") != repo or not SEMVER.fullmatch(version)
                    or release.get("version") != version or not SEMVER.fullmatch(release.get("tag", ""))
                    or SEMVER.fullmatch(release["tag"])[1] != version
                    or entry.get("source_url") != f"https://github.com/{repo}.git"
                    or not HEX40.fullmatch(entry.get("source_commit", ""))
                    or release.get("commit") != entry["source_commit"]
                    or entry.get("source_archive") != f"{package}-{version}.tar.gz"
                    or entry.get("source_epoch") != release.get("epoch")
                    or not isinstance(release.get("epoch"), int) or release["epoch"] < 0
                    or not HEX64.fullmatch(entry.get("source_sha256", ""))):
                raise SourceError(f"invalid locked application identity: {package}")
        n = lock.get("nikki", {})
        if (n.get("repository") != "nikkinikki-org/OpenWrt-nikki"
                or not HEX40.fullmatch(n.get("commit", ""))
                or not SEMVER.fullmatch(n.get("tag", ""))
                or SEMVER.fullmatch(n["tag"])[1] != n.get("version")
                or n.get("source_url") != f"https://codeload.github.com/nikkinikki-org/OpenWrt-nikki/tar.gz/{n['commit']}"
                or n.get("source_archive") != f"rc01-nikki-{n['commit']}.tar.gz"
                or not HEX64.fullmatch(n.get("source_sha256", ""))):
            raise SourceError("invalid locked Nikki stable source identity")
        if lock.get("with_dae"):
            dae = lock.get("optional_dae", {})
            if dae.get("feed_commit") != lock["feeds"]["daede"]["commit"]:
                raise SourceError("daemon source lock does not match this build's feed")
            archives = dae.get("source_archives", {})
            if set(archives) != {"dae", "daed"}:
                raise SourceError("missing locked daemon source archives")
            for name, item in archives.items():
                hashed = item.get("sha256", "")
                version = item.get("version", "")
                filename = f"{name}-src-{version}-{hashed[:12]}.tar.gz"
                if (not HEX64.fullmatch(hashed) or not re.fullmatch(r"20[0-9]{2}\.[0-9]{2}\.[0-9]{2}", version)
                        or item.get("filename") != filename
                        or item.get("url") != f"https://github.com/kenzok8/openwrt-daede/releases/download/{name}-src/{filename}"):
                    raise SourceError("invalid locked daemon archive identity")
    return lock


def load_lock():
    return validate_lock(json.loads((STATE / "lock.json").read_text()))


def file_snapshot(path):
    """Read a regular file without following a replaced leaf; retain its mode.

    lstat/open/fstat/lstat also detect a replacement while the snapshot is read.
    Missing is different from a directory, symlink, FIFO or another file type.
    """
    try:
        before = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(before.st_mode):
        raise SourceError("generated path is no longer a regular file")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        opened = os.fstat(stream.fileno())
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise SourceError("generated path changed type or identity while reading")
        content = stream.read()
        after_read = os.fstat(stream.fileno())
    after = path.lstat()
    identity = lambda item: (item.st_dev, item.st_ino, item.st_mode, item.st_size,
                             item.st_mtime_ns, item.st_ctime_ns)
    if identity(before) != identity(after_read) or identity(before) != identity(after):
        raise SourceError("generated path changed during snapshot")
    return {"content": content, "mode": stat.S_IMODE(after.st_mode), "identity": identity(after)}


def generated_feed_changes(previous, owned):
    for name in previous["feeds"]:
        feed = ROOT / "feeds" / name
        if run("git", "-C", str(feed), "diff", "--cached", "--name-only"):
            raise SourceError(f"staged local feed changes in {name}; preserve them before source resolution")
        changed = set(run("git", "-C", str(feed), "diff", "--name-only", "HEAD").splitlines())
        changed.update(run("git", "-C", str(feed), "ls-files", "--others", "--exclude-standard").splitlines())
        if any(f"feeds/{name}/{path}" not in owned for path in changed):
            raise SourceError(f"unrelated local feed changes in {name}; preserve them before source resolution")


def reset_generated(previous):
    """Restore only a prior resolver's intact outputs before moving feed HEADs.

    No checkout/reset operation may discard unrelated local feed work. All
    source pre/postimages and feed identities validate before restoring files.
    """
    validate_lock(previous)
    verify_feeds(previous)
    owned = set(previous.get("outputs", {}))
    generated_feed_changes(previous, owned)
    pending = []
    for name, item in previous.get("outputs", {}).items():
        path = safe_output(name)
        current = file_snapshot(path)
        actual_hash = digest(current["content"]) if current is not None else None
        if (actual_hash != item["after"] or
                (current is not None and current["mode"] != item["mode"])):
            raise SourceError(f"previous generated output has local content or permission changes: {name}")
        _, feed, *parts = PurePosixPath(name).parts
        relative = "/".join(parts)
        result = subprocess.run(["git", "-C", str(ROOT / "feeds" / feed), "show", "HEAD:"+relative], capture_output=True)
        before = result.stdout if result.returncode == 0 else None
        if (digest(before) if before is not None else None) != item["before"]:
            raise SourceError(f"previous source preimage no longer matches feed: {name}")
        mode = 0o644
        if before is not None:
            tree = run("git", "-C", str(ROOT / "feeds" / feed), "ls-tree", "HEAD", "--", relative)
            if not tree.startswith(("100644 ", "100755 ")):
                raise SourceError(f"unsupported original generated-path file type: {name}")
            mode = 0o755 if tree.startswith("100755 ") else 0o644
        pending.append((name, path, current, before, mode))
    # Repeat both the index and the complete filesystem checks immediately
    # before any write. A chmod or symlink replacement must not reset peers.
    generated_feed_changes(previous, owned)
    for name, path, current, _, _ in pending:
        safe_output(name)
        if file_snapshot(path) != current:
            raise SourceError("generated feed file changed during reset validation")
    for _, path, _, before, mode in pending:
        if before is None:
            path.unlink(missing_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(before)
            path.chmod(mode)


def plan(mode, replay=None):
    if STATE.is_symlink():
        raise SourceError("source state directory must not be a symlink")
    if replay:
        lock = json.loads(Path(replay).read_text())
        if not lock.get("prepared") or lock.get("schema") != 1:
            raise SourceError("replay needs a completed software source lock")
        validate_lock(lock)
        lock["replay"] = True
    else:
        lock = {"schema": 1, "mode": mode, "resolved_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "feeds": feed_plan(mode), "platform_inputs": platform_inputs(), "prepared": False,
                "with_dae": os.environ.get("RC01_WITH_DAE", "false") == "true",
                "source_date_epoch": int(os.environ.get("SOURCE_DATE_EPOCH") or run("git", "show", "-s", "--format=%ct", "HEAD", cwd=ROOT)),
                "build_commit": run("git", "rev-parse", "HEAD", cwd=ROOT)}
    if lock["with_dae"] != (os.environ.get("RC01_WITH_DAE", "false") == "true"):
        raise SourceError("replay daemon selection differs from requested variant")
    validate_lock(lock)
    if (STATE / "lock.json").exists():
        previous = json.loads((STATE / "lock.json").read_text())
        if previous.get("prepared"):
            reset_generated(previous)
        else:
            for name in lock["feeds"]:
                if (ROOT / "feeds" / name).is_dir():
                    generated_feed_changes({"feeds": {name: lock["feeds"][name]}}, set())
    else:
        for name in lock["feeds"]:
            if (ROOT / "feeds" / name).is_dir():
                generated_feed_changes({"feeds": {name: lock["feeds"][name]}}, set())
    for name in ("package-manifest.json", "dae-sources.json", "dae.patch"):
        path = STATE / name
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise SourceError("unexpected source-state artifact type")
        path.unlink(missing_ok=True)
    write_json(STATE / "lock.json", lock)
    (STATE / "feeds.conf").write_text(feeds_text(lock))
    (STATE / "environment.sh").write_text(f"export SOURCE_DATE_EPOCH={lock['source_date_epoch']}\n")
    write_preparation_phase(lock, "planned")


def field(text, key):
    matches = re.findall(r"^" + re.escape(key) + r":=(.*)$", text, re.M)
    if len(matches) != 1:
        raise SourceError(f"expected one literal {key}")
    return matches[0]


def replace_field(text, key, value):
    field(text, key)
    if "\n" in value or "\r" in value:
        raise SourceError("invalid recipe value")
    return re.sub(r"^" + re.escape(key) + r":=.*$", lambda _: key + ":=" + value, text, flags=re.M)


def compatible_recipe(current, baseline, keys):
    # Version/hash churn is allowed; install/build/dependency changes require
    # updating this checked-in adapter, not a permissive regex/fuzzy patch.
    def shape(text):
        for key in keys:
            text = replace_field(text, key, "<resolved>")
        return text
    if shape(current) != shape(baseline):
        raise SourceError("unsupported application recipe change; review its build/install/dependency adapter")


def archive_members(path):
    with tarfile.open(path) as stream:
        members = stream.getmembers()
        roots = {PurePosixPath(m.name).parts[0] for m in members if PurePosixPath(m.name).parts}
        if len(roots) != 1:
            raise SourceError("source archive must contain one root")
        root = next(iter(roots))
        for member in members:
            p = PurePosixPath(member.name)
            if p.is_absolute() or ".." in p.parts:
                raise SourceError("unsafe source archive path")
        result = {}
        for member in members:
            if member.isfile():
                result[str(PurePosixPath(member.name).relative_to(root))] = (stream.extractfile(member).read(), member.mode)
        return result


def download(url, path, expected=None):
    if not url.startswith(("https://github.com/", "https://codeload.github.com/", "https://go.dev/")):
        raise SourceError("unsupported application source origin")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and expected and digest(path.read_bytes()) == expected:
        return path
    request = urllib.request.Request(url, headers={"User-Agent": "RC01-software-source-resolver"})
    temporary = path.with_suffix(path.suffix + ".partial")
    try:
        with urllib.request.urlopen(request, timeout=120) as response, temporary.open("wb") as output:
            shutil.copyfileobj(response, output)
        actual = digest(temporary.read_bytes())
        if expected and actual != expected:
            raise SourceError(f"source SHA256 mismatch: {path.name}")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def git_archive_source(release, package, expected=None):
    """Use OpenWrt's rawgit pack contract; hash actual output, never hash=skip."""
    if (package not in ("tailscale", "mihomo-meta") or not SEMVER.fullmatch(release.get("version", ""))
            or not HEX40.fullmatch(release.get("commit", "")) or release.get("epoch", -1) < 0):
        raise SourceError("invalid immutable application source lock")
    github_repo(f"https://github.com/{release['repository']}.git")
    filename = f"{package}-{release['version']}.tar.gz"
    target = ROOT / "dl" / filename
    if expected and target.exists() and digest(target.read_bytes()) == expected:
        return target
    target.parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="rc01-app-source-") as temporary:
        work = Path(temporary)
        bare = work / "objects.git"
        subprocess.run(["git", "init", "--bare", "--quiet", str(bare)], check=True)
        url = f"https://github.com/{release['repository']}.git"
        subprocess.run(["git", "-C", str(bare), "fetch", "--quiet", "--depth=1", "--no-tags", url, release["commit"]], check=True)
        if run("git", "-C", str(bare), "rev-parse", "FETCH_HEAD") != release["commit"]:
            raise SourceError("downloaded application commit differs")
        if run("git", "-C", str(bare), "ls-tree", "-r", "FETCH_HEAD").find("160000 commit") >= 0:
            raise SourceError("application submodules require a reviewed archive adapter")
        archive = work / "source.tar"
        subprocess.run(["git", "-C", str(bare), "-c", "core.abbrev=8", "archive", "--format=tar", "FETCH_HEAD", "-o", str(archive)], check=True)
        folder = work / f"{package}-{release['version']}"
        folder.mkdir(mode=0o755)
        with tarfile.open(archive) as stream:
            stream.extractall(folder, filter="data")
        # data_filter intentionally ignores directory modes; normalize them
        # explicitly so a caller's umask=002 cannot alter the archive hash.
        for directory in [folder, *(p for p in folder.rglob("*") if p.is_dir() and not p.is_symlink())]:
            directory.chmod(0o755)
        (folder / ".gitmodules").unlink(missing_ok=True)
        # Same mode/owner/sort/epoch and gzip -n as include/download.mk.
        raw = work / "packed.tar"
        env = os.environ.copy()
        with raw.open("wb") as output:
            subprocess.run(["tar", "--numeric-owner", "--owner=0", "--group=0", "--mode=a-s", "--sort=name",
                            f"--mtime=@{release['epoch']}", "-C", str(work), "-c", folder.name],
                           stdout=output, check=True, env=env)
        packed = work / filename
        with packed.open("wb") as output:
            subprocess.run(["gzip", "-n", "-c", str(raw)], stdout=output, check=True)
        actual = digest(packed.read_bytes())
        if expected and actual != expected:
            raise SourceError(f"replayed application archive differs: {filename}: expected {expected}, got {actual}")
        shutil.copyfile(packed, target)
    return target


def go_requirement(members, compiler):
    content = members["go.mod"][0]
    match = re.search(rb"^go ([0-9]+\.[0-9]+(?:\.[0-9]+)?)$", content, re.M)
    if not match:
        raise SourceError("application source has no exact Go requirement")
    required = match[1].decode()
    version = lambda value: tuple(int(x) for x in value.split(".")) + (0,) * (3-len(value.split(".")))
    if version(required) > version(compiler):
        raise SourceError(f"application requires Go {required}, selected feed compiler is {compiler}")
    return required


def safe_output(name):
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or len(path.parts) < 3 or path.parts[0] != "feeds":
        raise SourceError("software lock attempted to write outside feeds")
    exact = {"feeds/packages/net/tailscale/Makefile", "feeds/packages/lang/golang/golang-compiler.mk",
             "feeds/packages/lang/golang/golang-host-build.mk", "feeds/nikki/mihomo-meta/Makefile",
             "feeds/daede/dae/Makefile", "feeds/daede/dae/files/dae.init", "feeds/daede/dae/files/dae-guard"}
    prefix = ("feeds/nikki/nikki/", "feeds/nikki/luci-app-nikki/", "feeds/daede/luci-app-daede/")
    if name not in exact and not name.startswith(prefix):
        raise SourceError("software lock attempted to write outside application adapter scope")
    target = ROOT / name
    feed_root = ROOT / "feeds" / path.parts[1]
    components = [target, *target.parents]
    if (any(item.is_symlink() for item in components[:components.index(feed_root) + 1])
            or not target.resolve().is_relative_to(feed_root)):
        raise SourceError("unsafe application output path")
    return target


def record_output(lock, name, before, after, mode=0o644):
    safe_output(name)
    lock.get("inputs", {}).pop(name, None)
    lock.setdefault("outputs", {})[name] = {"before": digest(before) if before is not None else None,
        "after": digest(after) if after is not None else None,
        "content": base64.b64encode(after).decode() if after is not None else None,
        "mode": 0o755 if mode & 0o111 else 0o644}


def apply_outputs(lock):
    pending = []
    for name, item in lock["outputs"].items():
        path = safe_output(name)
        current = path.read_bytes() if path.is_file() else None
        payload = base64.b64decode(item["content"], validate=True) if item["content"] is not None else None
        if (digest(payload) if payload is not None else None) != item["after"]:
            raise SourceError(f"corrupt lock postimage: {name}")
        current_hash = digest(current) if current is not None else None
        if current_hash not in (item["before"], item["after"]):
            raise SourceError(f"application pre/postimage drift: {name}")
        pending.append((path, current, payload, item["mode"]))
    # Nothing is written until every output validates.
    for path, before, _, _ in pending:
        if (path.read_bytes() if path.is_file() else None) != before:
            raise SourceError("feed changed during source preparation")
    for path, _, payload, mode in pending:
        if payload is None:
            path.unlink(missing_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
            path.chmod(mode)


def pristine(feed, name):
    return subprocess.check_output(["git", "-C", str(ROOT / "feeds" / feed), "show", "HEAD:" + name])


def verify_feeds(lock):
    for name, item in lock["feeds"].items():
        feed = ROOT / "feeds" / name
        if Path(run("git", "-C", str(feed), "rev-parse", "--show-toplevel")).resolve() != feed.resolve():
            raise SourceError(f"not an independent feed: {name}")
        if run("git", "-C", str(feed), "rev-parse", "HEAD") != item["commit"]:
            raise SourceError(f"feed is not at this build's locked commit: {name}")


def recipe_closure(feed, group, lock):
    manifest = json.loads((BASELINES / "recipe-closure.json").read_text())[group]
    tree = run("git", "-C", str(ROOT / "feeds" / feed), "ls-tree", "-r", "--format=%(objectmode) %(path)", "HEAD", "--",
               *manifest["roots"]).splitlines()
    entries = [line.split(" ", 1) for line in tree]
    names = [item[1] for item in entries]
    if set(names) != set(manifest["files"]) or len(names) != len(set(names)):
        raise SourceError(f"unsupported {group} build input set; review added/deleted files")
    for mode, name in entries:
        if mode != f"100{manifest['files'][name]['mode']:03o}":
            raise SourceError(f"unsupported {group} build input type/permission: {name}")
    payloads = {}
    for name, expected in manifest["files"].items():
        content = pristine(feed, name)
        baseline_name = expected.get("baseline")
        if baseline_name:
            baseline = (BASELINES / baseline_name).read_bytes()
            if digest(baseline) != expected["sha256"]:
                raise SourceError(f"recipe baseline differs from closure manifest: {baseline_name}")
            if name == "ci/pins.env":
                normalize_pins(content.decode(), baseline.decode())
            else:
                compatible_recipe(content.decode(), baseline.decode(), expected.get("fields", []))
        elif digest(content) != expected["sha256"]:
            raise SourceError(f"unsupported {group} build/lifecycle input change: {name}")
        payloads[name] = content
        lock.setdefault("inputs", {})[f"feeds/{feed}/{name}"] = digest(content)
    return payloads


def normalize_pins(current, baseline):
    keys = re.findall(r"^([A-Z_]+)=", baseline, re.M)
    def shape(text):
        found = re.findall(r"^([A-Z_]+)=", text, re.M)
        if found != keys:
            raise SourceError("unsupported daemon component-pin schema")
        values = {}
        for key in keys:
            matches = re.findall(r"^" + key + r"=(.*)$", text, re.M)
            if len(matches) != 1:
                raise SourceError("duplicate daemon component pin")
            value = matches[0]
            valid = re.fullmatch(r"20[0-9]{2}\.[0-9]{2}\.[0-9]{2}", value) if key.endswith("_VERSION") else HEX40.fullmatch(value)
            if not valid:
                raise SourceError(f"invalid daemon component pin: {key}")
            values[key] = value
            text = re.sub(r"^" + key + r"=.*$", key + "=<resolved>", text, flags=re.M)
        return text, values
    baseline_shape, _ = shape(baseline)
    current_shape, values = shape(current)
    if current_shape != baseline_shape:
        raise SourceError("unsupported daemon pin-file structure")
    return values


def go_adapter(lock):
    payloads = recipe_closure("packages", "go", lock)
    files = ["lang/golang/golang-compiler.mk", "lang/golang/golang-host-build.mk"]
    patch = ROOT / "config/rc01/patches/golang/100-rc01-local-toolchain.patch"
    with tempfile.TemporaryDirectory(prefix="rc01-go-adapter-") as temporary:
        work = Path(temporary)
        for name in files:
            payload = pristine("packages", name)
            if payload != (BASELINES / PurePosixPath(name).name).read_bytes():
                raise SourceError(f"unsupported Go build helper change: {name}; review local GOTOOLCHAIN guard")
            path = work / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
        subprocess.run(["git", "apply", "--check", str(patch)], cwd=work, check=True)
        subprocess.run(["git", "apply", str(patch)], cwd=work, check=True)
        for name in files:
            record_output(lock, "feeds/packages/" + name, pristine("packages", name), (work / name).read_bytes())
    values = payloads["lang/golang/golang-values.mk"].decode()
    default = re.search(r"^GO_DEFAULT_VERSION\s*:?=\s*([0-9]+\.[0-9]+)\s*$", values, re.M)
    if not default or default[1] != "1.27":
        raise SourceError("new Go major/minor needs bootstrap compatibility review; no toolchain fallback")
    recipe = payloads["lang/golang/golang1.27/Makefile"].decode()
    compiler = field(recipe, "GO_VERSION_MAJOR_MINOR") + "." + field(recipe, "GO_VERSION_PATCH")
    if field(recipe, "GO_VERSION_RC") or not re.fullmatch(r"1\.27\.[0-9]+", compiler):
        raise SourceError("unsupported/prerelease Go compiler")
    bootstrap = payloads["lang/golang/golang-bootstrap/Makefile"].decode()
    if (field(bootstrap, "GO_VERSION_MAJOR_MINOR") + "." + field(bootstrap, "GO_VERSION_PATCH") != "1.24.13"
            or field(recipe, "GO_BOOTSTRAP_VERSION") != "bootstrap"):
        raise SourceError("feed Go bootstrap chain differs from the verified 1.24.13 strategy")
    if not HEX64.fullmatch(field(recipe, "PKG_HASH")) or not HEX64.fullmatch(field(bootstrap, "PKG_HASH")):
        raise SourceError("Go source hash is not a real SHA256")
    if not re.fullmatch(r"[0-9]+", field(recipe, "PKG_RELEASE")) or not re.fullmatch(r"[0-9]+", field(bootstrap, "PKG_RELEASE")):
        raise SourceError("invalid Go compiler/bootstrap package release")
    lock["go"] = {"version": compiler, "bootstrap": "1.24.13", "recipe_sha256": digest(recipe.encode()),
                  "local_patch_sha256": digest(patch.read_bytes())}


def canonical_release(release):
    return {key: release[key] for key in ("repository", "tag", "version", "commit", "epoch")}


def apps_adapter(lock, releases=None):
    manifest = []
    for repo, feed, path, package, baseline, keys in [
        ("tailscale/tailscale", "packages", "net/tailscale/Makefile", "tailscale", "tailscale.Makefile", ["PKG_VERSION", "PKG_RELEASE", "PKG_HASH"]),
        ("MetaCubeX/mihomo", "nikki", "mihomo-meta/Makefile", "mihomo-meta", "mihomo.Makefile", ["PKG_VERSION", "PKG_SOURCE_VERSION", "PKG_MIRROR_HASH", "PKG_BUILD_VERSION"])]:
        before = pristine(feed, path)
        current = before.decode()
        compatible_recipe(current, (BASELINES / baseline).read_text(), keys)
        resolved = releases[(feed, path)] if releases else None
        release = canonical_release(resolved["release"] if resolved else stable_release(repo))
        source = git_archive_source(release, package, resolved["source_sha256"] if resolved else None)
        members = archive_members(source)
        required = go_requirement(members, lock["go"]["version"])
        hashed = digest(source.read_bytes())
        after = replace_field(current, "PKG_VERSION", release["version"])
        if package == "tailscale":
            after = replace_field(after, "PKG_RELEASE", "1")
            after = replace_field(after, "PKG_SOURCE_URL", "https://github.com/tailscale/tailscale.git")
            after = replace_field(after, "PKG_HASH", hashed).replace("PKG_HASH:=", "PKG_MIRROR_HASH:=", 1)
            after = after.replace("PKG_SOURCE_URL:=", "PKG_SOURCE_SUBDIR:=$(PKG_NAME)-$(PKG_VERSION)\nPKG_SOURCE_PROTO:=git\nPKG_SOURCE_VERSION:=" + release["commit"] + "\nPKG_SOURCE_URL:=", 1)
        else:
            after = replace_field(after, "PKG_SOURCE_VERSION", release["commit"])
            after = replace_field(after, "PKG_MIRROR_HASH", hashed)
            after = replace_field(after, "PKG_BUILD_VERSION", release["tag"])
            if after.count(OLD_TIME) != 1:
                raise SourceError("Mihomo SOURCE_DATE_EPOCH preimage changed")
            after = after.replace(OLD_TIME, NEW_TIME)
        record_output(lock, f"feeds/{feed}/{path}", before, after.encode())
        manifest.append({"feed": feed, "feed_revision": lock["feeds"][feed]["commit"], "path": path,
            "version": release["version"], "source_commit": release["commit"],
            "source_url": f"https://github.com/{repo}.git", "source_sha256": hashed,
            "source_archive": source.name, "source_epoch": release["epoch"], "release": release,
            "go": required, "go_mod_sha256": digest(members["go.mod"][0]),
            "baseline_sha256": digest(before), "applied_sha256": digest(after.encode()),
            "source_hash_method": "OpenWrt rawgit deterministic archive; actual SHA256"})
    lock["package_manifest"] = {"packages": manifest}


def nikki_adapter(lock, release=None):
    expected_hash = release.get("source_sha256") if release else None
    release = canonical_release(release or stable_release("nikkinikki-org/OpenWrt-nikki"))
    url = f"https://codeload.github.com/{release['repository']}/tar.gz/{release['commit']}"
    archive = download(url, ROOT / "dl" / f"rc01-nikki-{release['commit']}.tar.gz", expected_hash)
    members = archive_members(archive)
    selected = {name: value for name, value in members.items() if name.startswith(("nikki/", "luci-app-nikki/"))}
    if not selected or "luci-app-nikki/Makefile" not in selected:
        raise SourceError("Nikki stable release lacks supported source packages")
    if field(selected["luci-app-nikki/Makefile"][0].decode(), "PKG_VERSION") != release["version"]:
        raise SourceError("Nikki stable tag and LuCI recipe version disagree")
    existing = run("git", "-C", str(ROOT / "feeds/nikki"), "ls-tree", "-r", "--name-only", "HEAD", "--", "nikki", "luci-app-nikki").splitlines()
    for name in sorted(set(existing) | set(selected)):
        before = pristine("nikki", name) if name in existing else None
        after, mode = selected.get(name, (None, 0o644))
        record_output(lock, "feeds/nikki/" + name, before, after, mode)
    lock["nikki"] = {**release, "source_url": url, "source_archive": archive.name,
                     "source_sha256": digest(archive.read_bytes()), "policy": "latest stable release source; software feed HEAD retained"}


def dae_adapter(lock, replay=False):
    if not lock["with_dae"]:
        return
    original = ROOT / "config/rc01/patches/daede/100-rc01-dae-lifecycle-and-source-policy.patch"
    pinned = json.loads((original.parent / "sources.json").read_text())
    payloads = recipe_closure("daede", "daede", lock)
    # New feed revisions can change versions/hashes. The lifecycle patch must
    # still apply exactly; never fuzz, omit the guard, or accept hash=skip.
    recipe = payloads["dae/Makefile"].decode()
    version = field(recipe, "PKG_VERSION")
    rel = field(recipe, "PKG_RELEASE")
    if not re.fullmatch(r"20[0-9]{2}\.[0-9]{2}\.[0-9]{2}", version) or not re.fullmatch(r"[0-9]+", rel):
        raise SourceError("unrecognized daede composite-source release")
    patch = original.read_text().replace(" PKG_VERSION:=2026.11.09\n-PKG_RELEASE:=2\n+PKG_RELEASE:=3",
        f" PKG_VERSION:={version}\n-PKG_RELEASE:={rel}\n+PKG_RELEASE:={int(rel)+1}")
    ui = payloads["luci-app-daede/Makefile"].decode()
    ui_version, ui_release = field(ui, "PKG_VERSION"), field(ui, "PKG_RELEASE")
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,2}", ui_version) or not re.fullmatch(r"[0-9]+", ui_release):
        raise SourceError("invalid daemon LuCI version/package release")
    patch = patch.replace(" PKG_VERSION:=1.15\n-PKG_RELEASE:=13\n+PKG_RELEASE:=14",
        f" PKG_VERSION:={ui_version}\n-PKG_RELEASE:={ui_release}\n+PKG_RELEASE:={int(ui_release)+1}")
    old_filename = pinned["source_archives"]["dae"]["filename"]
    patch = patch.replace(" PKG_SOURCE:=" + old_filename, " PKG_SOURCE:=" + field(recipe, "PKG_SOURCE"))
    files = pinned["files"]
    with tempfile.TemporaryDirectory(prefix="rc01-daede-adapter-") as temporary:
        work = Path(temporary)
        for name, item in files.items():
            if item["before"] is not None:
                path = work / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(pristine("daede", name))
                if name.endswith("/update-pkg.sh"):
                    path.chmod(0o755)
        patchfile = work / "local.patch"
        patchfile.write_text(patch)
        subprocess.run(["git", "apply", "--check", str(patchfile)], cwd=work, check=True)
        subprocess.run(["git", "apply", str(patchfile)], cwd=work, check=True)
        actual = {"schema": 1, "feed_url": pinned["feed_url"],
                  "integration": "experimental composite source recipe; not official dae releases/latest",
                  "physical_acceptance": "not-performed", "source_archives": {},
                  "kernel_btf": {"source": "actual-built-kernel", "shadow_vmlinux_btf_allowed": False}}
        actual["feed_commit"] = lock["feeds"]["daede"]["commit"]
        actual["patches"] = [{"name": original.name, "sha256": digest(patch.encode())}]
        actual["files"] = {}
        for name, item in files.items():
            before = pristine("daede", name) if item["before"] is not None else None
            after = (work / name).read_bytes()
            actual["files"][name] = {"before": digest(before) if before is not None else None, "after": digest(after)}
            if before != after:
                record_output(lock, "feeds/daede/" + name, before, after, (work / name).stat().st_mode)
            else:
                lock.setdefault("inputs", {})["feeds/daede/" + name] = digest(after)
        actual["upstream_pins"] = normalize_pins(payloads["ci/pins.env"].decode(),
                                                   (BASELINES / "daede-pins.env").read_text())
        for name in ("dae", "daed"):
            text = payloads[name + "/Makefile"].decode()
            if actual["upstream_pins"][name.upper() + "_VERSION"] != field(text, "PKG_VERSION"):
                raise SourceError("daemon published recipe and assembly version pins disagree")
            if not re.fullmatch(r"[0-9]+", field(text, "PKG_RELEASE")):
                raise SourceError("invalid daemon package release")
            hashed = field(text, "PKG_HASH")
            filename = field(text, "PKG_SOURCE")
            source_url = field(text, "PKG_SOURCE_URL")
            if not HEX64.fullmatch(hashed) or source_url != f"https://github.com/kenzok8/openwrt-daede/releases/download/{name}-src":
                raise SourceError("unsupported daede source URL/hash")
            if filename != f"{name}-src-{field(text,'PKG_VERSION')}-{hashed[:12]}.tar.gz":
                raise SourceError("daede asset is not content-addressed")
            if not replay:
                release = api(f"repos/kenzok8/openwrt-daede/releases/tags/{name}-src")
                if release.get("draft") or release.get("prerelease"):
                    raise SourceError("daede source asset is not a published stable recipe asset")
                assets = [a for a in release["assets"] if a["name"] == filename]
                if len(assets) != 1 or assets[0].get("digest") != "sha256:" + hashed:
                    raise SourceError("daede release asset digest differs from feed recipe")
            url = source_url + "/" + filename
            source = download(url, ROOT / "dl" / filename, hashed)
            members = archive_members(source)
            modpath = "core/go.mod" if name == "dae" else "wing/go.mod"
            go_requirement({"go.mod": members[modpath]}, lock["go"]["version"])
            actual["source_archives"][name] = {"version": field(text, "PKG_VERSION"),
                "feed_release": int(field(text, "PKG_RELEASE")), "local_release": int(rel)+1 if name == "dae" else int(field(text,"PKG_RELEASE")),
                "filename": filename, "url": url, "sha256": hashed, "size": source.stat().st_size,
                "module_files": {p: digest(v[0]) for p,v in members.items() if p.endswith(("go.mod", "go.sum"))}}
        actual["integration"] = "experimental composite source recipe; not official dae releases/latest"
        lock["optional_dae"] = actual
        lock["dae_patch"] = patch


def replay_sources(lock):
    # Re-derive outputs from fixed public source commits and this checkout's
    # reviewed adapters. The replay artifact cannot supply arbitrary recipe
    # code merely by recomputing its own postimage hash.
    derived = {key: value for key, value in lock.items() if key not in
               ("outputs", "inputs", "package_manifest", "optional_dae", "nikki", "go", "dae_patch")}
    go_adapter(derived)
    apps_adapter(derived, {(e["feed"], e["path"]): e for e in lock["package_manifest"]["packages"]})
    nikki_adapter(derived, lock["nikki"])
    dae_adapter(derived, replay=True)
    keys = ("outputs", "inputs", "package_manifest", "go", "optional_dae", "dae_patch", "nikki")
    for key in keys:
        if derived.get(key) != lock.get(key):
            raise SourceError(f"replay {key} differ from the fixed source/adapters; review compatibility")
    for key in keys:
        if key in derived:
            lock[key] = derived[key]
        else:
            lock.pop(key, None)


def ensure_baseline_objects():
    # The post-build regression suite exercises the original exact recipe
    # guards too. A shallow latest-feed clone need not contain those objects;
    # fetch them without moving the source commit selected for this build.
    for name, item in feed_plan("locked").items():
        if name not in ("packages", "nikki"):
            continue
        feed = ROOT / "feeds" / name
        probe = subprocess.run(["git", "-C", str(feed), "cat-file", "-e", item["commit"]+"^{commit}"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if probe.returncode:
            subprocess.run(["git", "-C", str(feed), "fetch", "--quiet", "--no-tags", "--depth=1",
                            item["url"], item["commit"]], check=True)


def prepare():
    lock = load_lock()
    phase = preparation_phase(lock)
    if phase != "planned":
        if phase in (None, "complete") and lock.get("prepared"):
            verify(lock)
            return
        raise SourceError("source preparation requires a current plan")
    verify_feeds(lock)
    ensure_baseline_objects()
    if lock["mode"] == "locked":
        # Existing exact baseline patch guards remain the locked-mode adapter.
        write_preparation_phase(lock, "locked-pending")
        return
    if lock.get("replay"):
        if not lock.get("prepared"):
            raise SourceError("incomplete replay source lock")
        replay_sources(lock)
    else:
        if lock.get("prepared"):
            raise SourceError("latest sources already resolved; use verify or begin a new explicit plan")
        go_adapter(lock)
        apps_adapter(lock)
        nikki_adapter(lock)
        dae_adapter(lock)
    validate_lock(lock)
    apply_outputs(lock)
    lock["prepared"] = True
    write_json(STATE / "lock.json", lock)
    write_source_artifacts(lock)
    verify(lock)
    write_preparation_phase(lock, "complete")


def write_source_artifacts(lock):
    write_json(STATE / "package-manifest.json", lock["package_manifest"])
    if lock.get("optional_dae"):
        write_json(STATE / "dae-sources.json", lock["optional_dae"])
        (STATE / "dae.patch").write_text(lock["dae_patch"])
    else:
        for name in ("dae-sources.json", "dae.patch"):
            (STATE / name).unlink(missing_ok=True)


def verify(lock=None):
    lock = validate_lock(lock) if lock is not None else load_lock()
    verify_feeds(lock)
    if not lock.get("prepared"):
        raise SourceError("software sources have not completed preparation")
    for name, expected in lock.get("inputs", {}).items():
        path = ROOT / name
        if not name.startswith("feeds/") or ".." in PurePosixPath(name).parts or path.is_symlink():
            raise SourceError("unsafe source-lock input")
        if digest(path.read_bytes()) != expected:
            raise SourceError(f"prepared software input differs from build lock: {name}")
    for name, item in lock.get("outputs", {}).items():
        current = file_snapshot(safe_output(name))
        actual = digest(current["content"]) if current is not None else None
        if actual != item["after"] or (current is not None and current["mode"] != item["mode"]):
            raise SourceError(f"prepared software source differs from build lock: {name}")
    return lock


def locked_sources(lock):
    """Rebuild locked-mode provenance from reviewed sources, never live diffs.

    The legacy patch entry points run first. Reconstruct their expected bytes
    in isolation so capture/replay cannot bless an arbitrary changed feed file.
    No release query or source download is needed for this reviewed baseline.
    """
    go_adapter(lock)
    directory = ROOT / "config/rc01/patches/packages"
    manifest = json.loads((directory / "manifest.json").read_text())
    for entry in manifest["packages"]:
        if lock["feeds"][entry["feed"]]["commit"] != entry["feed_revision"]:
            raise SourceError("locked application recipe belongs to another feed revision")
        before = pristine(entry["feed"], entry["path"])
        adjusted = before.replace(OLD_TIME.encode(), NEW_TIME.encode()) if entry["feed"] == "nikki" else before
        if digest(adjusted) != entry["baseline_sha256"]:
            raise SourceError("locked application baseline differs from reviewed manifest")
        with tempfile.TemporaryDirectory(prefix="rc01-locked-package-") as temporary:
            work = Path(temporary)
            target = work / entry["path"]
            target.parent.mkdir(parents=True)
            target.write_bytes(adjusted)
            patch = str(directory / entry["patch"])
            subprocess.run(["git", "apply", "--check", patch], cwd=work, check=True)
            subprocess.run(["git", "apply", patch], cwd=work, check=True)
            after = target.read_bytes()
        if digest(after) != entry["applied_sha256"]:
            raise SourceError("locked application output differs from reviewed manifest")
        record_output(lock, f"feeds/{entry['feed']}/{entry['path']}", before, after)
    lock["package_manifest"] = manifest
    if not lock["with_dae"]:
        return
    recipe_closure("daede", "daede", lock)
    directory = ROOT / "config/rc01/patches/daede"
    pinned = json.loads((directory / "sources.json").read_text())
    if lock["feeds"]["daede"]["commit"] != pinned["feed_commit"]:
        raise SourceError("locked daemon feed differs from reviewed manifest")
    if {p.name for p in directory.glob("*.patch")} != {p["name"] for p in pinned["patches"]}:
        raise SourceError("locked daemon patch set differs from reviewed manifest")
    with tempfile.TemporaryDirectory(prefix="rc01-locked-daede-") as temporary:
        work = Path(temporary)
        for name, item in pinned["files"].items():
            before = pristine("daede", name) if item["before"] is not None else None
            if (digest(before) if before is not None else None) != item["before"]:
                raise SourceError("locked daemon preimage differs from reviewed manifest")
            if before is not None:
                target = work / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(before)
                if name.endswith("/update-pkg.sh"):
                    target.chmod(0o755)
        patches = []
        for item in pinned["patches"]:
            patch = directory / item["name"]
            if digest(patch.read_bytes()) != item["sha256"]:
                raise SourceError("locked daemon patch differs from reviewed manifest")
            subprocess.run(["git", "apply", "--check", str(patch)], cwd=work, check=True)
            subprocess.run(["git", "apply", str(patch)], cwd=work, check=True)
            patches.append(patch.read_text())
        for name, item in pinned["files"].items():
            after = (work / name).read_bytes()
            if digest(after) != item["after"]:
                raise SourceError("locked daemon output differs from reviewed manifest")
            if item["before"] != item["after"]:
                before = pristine("daede", name) if item["before"] is not None else None
                record_output(lock, "feeds/daede/" + name, before, after, (work / name).stat().st_mode)
    lock["optional_dae"] = pinned
    lock["dae_patch"] = "".join(patches)


def finish_locked():
    lock = load_lock()
    if lock["mode"] != "locked":
        return
    phase = preparation_phase(lock)
    if phase != "locked-pending":
        if phase in (None, "complete") and lock.get("prepared"):
            verify(lock)
            return
        raise SourceError("locked finish requires this plan's verified preparation phase")
    keys = ("outputs", "inputs", "package_manifest", "go", "optional_dae", "dae_patch", "nikki")
    derived = {key: value for key, value in lock.items() if key not in keys}
    locked_sources(derived)
    if lock.get("replay"):
        for key in keys:
            if derived.get(key) != lock.get(key):
                raise SourceError(f"locked replay {key} differ from reviewed sources/adapters")
    generated_feed_changes(derived, set(derived["outputs"]))
    # Legacy locked adapters preserve pre-existing group-write bits. Check
    # every produced byte first, then give this build's generated recipes the
    # same canonical modes as latest/replay. Prior user chmod changes were
    # already rejected by plan/reset before these adapters ran.
    for name, item in derived["outputs"].items():
        current = file_snapshot(safe_output(name))
        actual = digest(current["content"]) if current is not None else None
        if actual != item["after"]:
            raise SourceError(f"locked adapter output differs from reviewed bytes: {name}")
    apply_outputs(derived)
    derived["prepared"] = True
    verify(derived)
    write_json(STATE / "lock.json", derived)
    write_source_artifacts(derived)
    write_preparation_phase(derived, "complete")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "prepare", "verify", "finish-locked"))
    parser.add_argument("--mode", choices=("latest", "locked"), default=os.environ.get("RC01_UPDATE_MODE", "latest"))
    parser.add_argument("--replay", default=os.environ.get("RC01_REPLAY_LOCK"))
    args = parser.parse_args()
    try:
        if args.command == "plan":
            plan(args.mode, args.replay)
        elif args.command == "prepare":
            prepare()
        elif args.command == "finish-locked":
            finish_locked()
        else:
            verify()
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        print(f"RC01 software source resolution failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
