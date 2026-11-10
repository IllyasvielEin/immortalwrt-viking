# SPDX-License-Identifier: GPL-2.0-only
"""Exercise bootstrap verification and the pinned Make calls without downloads."""

import hashlib
import importlib.util
import io
import json
import os
import re
from contextlib import redirect_stdout
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/rc01-go-bootstrap.py"
PATCH_SCRIPT = ROOT / "scripts/patch-rc01-golang.sh"
PATCH = ROOT / "config/rc01/patches/golang/100-rc01-local-toolchain.patch"
FEED_REVISION = "48f609040f2e4beb16c1aa62af6a6aaafd2eb6cd"
spec = importlib.util.spec_from_file_location("rc01_go_bootstrap", SCRIPT)
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)
GO_VALIDATOR = ROOT / "scripts/validate-rc01-go-version.py"
compiler_spec = importlib.util.spec_from_file_location("rc01_go_version", GO_VALIDATOR)
compiler = importlib.util.module_from_spec(compiler_spec)
compiler_spec.loader.exec_module(compiler)

# This executable computes its own root and records the environment actually
# received by subprocesses; no mock stands in for the Go command invocation.
GO_FIXTURE = r'''#!/bin/sh
set -eu
root="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)"
printf '%s|%s|%s|%s\n' "$GOTOOLCHAIN" "$GOENV" "${GOROOT-unset}" "$*" >> "$RC01_GO_TEST_LOG"
case "$*" in
    version) printf '%s\n' "${RC01_TEST_VERSION:-go version goVERSION linux/amd64}" ;;
    'env GOROOT GOHOSTOS GOHOSTARCH')
        printf '%s\n' "${RC01_TEST_REPORTED_ROOT:-$root}" "${RC01_TEST_HOST_OS:-linux}" "${RC01_TEST_HOST_ARCH:-amd64}" ;;
    *) exit 2 ;;
esac
'''.replace("goVERSION", "go" + bootstrap.VERSION).encode()


def archive_bytes(omit=(), extra=(), executable=True):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for name in ("go", "go/bin", "go/pkg", "go/src"):
            if name in omit:
                continue
            member = tarfile.TarInfo(name)
            member.type = tarfile.DIRTYPE
            member.mode = 0o755
            archive.addfile(member)
        for name, content in (("go/bin/go", GO_FIXTURE), ("go/LICENSE", b"fixture license\n")):
            if name in omit:
                continue
            member = tarfile.TarInfo(name)
            member.mode = 0o755 if name.endswith("/go") and executable else 0o644
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
        for member in extra:
            archive.addfile(member)
    return output.getvalue()


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="rc01-go-test-")
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.log = self.work / "go-invocations"
        self.environment = mock.patch.dict(os.environ, {
            "RC01_GO_TEST_LOG": str(self.log), "GOTOOLCHAIN": "auto",
            "GOENV": "/untrusted/goenv", "GOROOT": "/untrusted/root",
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def install_fixture(self, data, name="install", sha=None, size=None):
        with mock.patch.object(bootstrap, "ARCHIVE_SHA256", sha or hashlib.sha256(data).hexdigest()), \
                mock.patch.object(bootstrap, "ARCHIVE_SIZE", len(data) if size is None else size), \
                mock.patch.object(bootstrap.urllib.request, "urlopen", return_value=io.BytesIO(data)) as request:
            result = bootstrap.install(self.work / name)
            request.assert_called_once_with(bootstrap.ARCHIVE_URL, timeout=60)
            return result

    def make_root(self):
        destination = self.work / "tree"
        destination.mkdir()
        archive = self.work / "fixture.tar.gz"
        archive.write_bytes(archive_bytes())
        bootstrap.extract(archive, destination)
        return destination / "go"

    def test_verified_install_allows_internal_links_and_cli_checks_actual_environment(self):
        symbolic = tarfile.TarInfo("go/src/license")
        symbolic.type, symbolic.linkname = tarfile.SYMTYPE, "../LICENSE"
        hard = tarfile.TarInfo("go/LICENSE.copy")
        hard.type, hard.linkname = tarfile.LNKTYPE, "go/LICENSE"
        root = self.install_fixture(archive_bytes(extra=(symbolic, hard)))
        self.assertEqual(root, self.work / "install/go")
        self.assertEqual((root / "src/license").read_bytes(), b"fixture license\n")
        self.assertEqual((root / "LICENSE.copy").read_bytes(), b"fixture license\n")
        result = subprocess.run([sys.executable, "-B", str(SCRIPT), "check", str(root)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, str(root) + "\n")
        self.assertEqual(self.log.read_text().splitlines(), [
            "local|off|unset|version", "local|off|unset|env GOROOT GOHOSTOS GOHOSTARCH",
        ] * 2)

    def test_install_cli_prints_only_the_installed_root(self):
        data = archive_bytes()
        destination = self.work / "cli-install"
        output = io.StringIO()
        with mock.patch.object(sys, "argv", [str(SCRIPT), "install", str(destination)]), \
                mock.patch.object(bootstrap, "ARCHIVE_SHA256", hashlib.sha256(data).hexdigest()), \
                mock.patch.object(bootstrap, "ARCHIVE_SIZE", len(data)), \
                mock.patch.object(bootstrap.urllib.request, "urlopen", return_value=io.BytesIO(data)), \
                redirect_stdout(output):
            self.assertEqual(bootstrap.main(), 0)
        self.assertEqual(output.getvalue(), str(destination / "go") + "\n")

    def test_bad_sha_or_size_never_opens_archive_or_executes_go(self):
        data = archive_bytes()
        for index, arguments in enumerate(({"sha": "0" * 64}, {"size": len(data) + 1}, {"size": len(data) - 1})):
            with self.subTest(arguments=arguments), \
                    mock.patch.object(bootstrap.tarfile, "open", side_effect=AssertionError("must not extract")):
                with self.assertRaises(bootstrap.BootstrapError):
                    self.install_fixture(data, name=f"bad-{index}", **arguments)
                self.assertEqual(list((self.work / f"bad-{index}").iterdir()), [])
                self.assertFalse(self.log.exists())

    def test_missing_structure_and_nonexecutable_go_fail_without_execution(self):
        variants = [{"omit": (name,)} for name in ("go/bin/go", "go/pkg", "go/src")]
        variants.append({"executable": False})
        for index, arguments in enumerate(variants):
            with self.subTest(arguments=arguments), self.assertRaises(bootstrap.BootstrapError):
                self.install_fixture(archive_bytes(**arguments), name=f"missing-{index}")
        self.assertFalse(self.log.exists())

    def test_archive_escape_and_special_members_are_rejected_before_execution(self):
        members = [tarfile.TarInfo(name) for name in ("../escaped", "/escaped", "go/../../escaped", "other/file")]
        for kind, target in ((tarfile.SYMTYPE, "../../escaped"), (tarfile.LNKTYPE, "../escaped")):
            member = tarfile.TarInfo("go/escape")
            member.type, member.linkname = kind, target
            members.append(member)
        device = tarfile.TarInfo("go/device")
        device.type = tarfile.CHRTYPE
        members.append(device)
        for index, member in enumerate(members):
            with self.subTest(name=member.name, kind=member.type):
                with self.assertRaises((bootstrap.BootstrapError, tarfile.TarError)):
                    self.install_fixture(archive_bytes(extra=(member,)), name=f"escape-{index}")
                self.assertEqual(list((self.work / f"escape-{index}").iterdir()), [])
        self.assertFalse((self.work / "escaped").exists())
        self.assertFalse(self.log.exists())

    def test_link_chain_cannot_escape_go_root_within_staging(self):
        first = tarfile.TarInfo("go/a")
        first.type, first.linkname = tarfile.SYMTYPE, "."
        second = tarfile.TarInfo("go/b")
        second.type, second.linkname = tarfile.SYMTYPE, "a/../escaped"
        with self.assertRaises(bootstrap.BootstrapError):
            self.install_fixture(archive_bytes(extra=(first, second)))
        self.assertFalse(self.log.exists())

    def test_wrong_version_platform_and_reported_root_are_rejected(self):
        root = self.make_root()
        variants = (
            {"RC01_TEST_VERSION": "go version go1.24.12 linux/amd64"},
            {"RC01_TEST_VERSION": f"go version go{bootstrap.VERSION} linux/arm64"},
            {"RC01_TEST_HOST_OS": "darwin"}, {"RC01_TEST_HOST_ARCH": "arm64"},
            {"RC01_TEST_REPORTED_ROOT": str(self.work)},
        )
        for environment in variants:
            with self.subTest(environment=environment), mock.patch.dict(os.environ, environment):
                with self.assertRaises(bootstrap.BootstrapError):
                    bootstrap.check(root)
        self.assertTrue(self.log.exists())

    def test_nonempty_destination_and_unsafe_paths_never_download(self):
        destination = self.work / "occupied"
        destination.mkdir()
        (destination / "keep").write_text("preserve")
        alias = self.work / "alias"
        alias.symlink_to(destination, target_is_directory=True)
        with mock.patch.object(bootstrap.urllib.request, "urlopen") as request:
            for value in (str(destination), str(alias), "/", "relative/path", str(self.work) + "/../unsafe",
                          str(self.work) + "/a b", str(self.work) + "/$(command)"):
                with self.subTest(path=value), self.assertRaises(bootstrap.BootstrapError):
                    bootstrap.install(value)
            request.assert_not_called()
        self.assertEqual((destination / "keep").read_text(), "preserve")

    def test_configuration_switches_external_to_source_and_checks_normalized_result(self):
        root = self.make_root()
        config = self.work / ".config"
        config.write_text('CONFIG_UNRELATED=y\nCONFIG_GOLANG_BUILD_BOOTSTRAP=y\n'
                          'CONFIG_GOLANG_EXTERNAL_BOOTSTRAP_ROOT="/stale"\n')
        bootstrap.configure(config, str(root))
        self.assertEqual(config.read_text(), 'CONFIG_UNRELATED=y\n'
                         '# CONFIG_GOLANG_BUILD_BOOTSTRAP is not set\n'
                         f'CONFIG_GOLANG_EXTERNAL_BOOTSTRAP_ROOT="{root}"\n')
        bootstrap.validate_config(config, str(root))
        with self.assertRaises(bootstrap.BootstrapError):
            bootstrap.validate_config(config, "")
        with mock.patch.object(bootstrap, "check", side_effect=AssertionError("source must not probe Go")):
            bootstrap.configure(config, "")
            bootstrap.validate_config(config, "")
        self.assertEqual(config.read_text(), 'CONFIG_UNRELATED=y\nCONFIG_GOLANG_BUILD_BOOTSTRAP=y\n'
                         'CONFIG_GOLANG_EXTERNAL_BOOTSTRAP_ROOT=""\n')

    def test_invalid_external_configuration_cannot_fall_back_or_modify_config(self):
        root = self.make_root()
        config = self.work / ".config"
        original = 'CONFIG_GOLANG_BUILD_BOOTSTRAP=y\nCONFIG_GOLANG_EXTERNAL_BOOTSTRAP_ROOT=""\n'
        config.write_text(original)
        with mock.patch.dict(os.environ, {"RC01_TEST_VERSION": "wrong"}):
            with self.assertRaises(bootstrap.BootstrapError):
                bootstrap.configure(config, str(root))
        self.assertEqual(config.read_text(), original)
        bootstrap.configure(config, str(root))
        expected = config.read_text()
        for invalid in (expected + 'CONFIG_GOLANG_BUILD_BOOTSTRAP=y\n',
                        expected.replace('# CONFIG_GOLANG_BUILD_BOOTSTRAP is not set', 'CONFIG_GOLANG_BUILD_BOOTSTRAP=y'),
                        expected.replace(str(root), str(self.work / "elsewhere"))):
            config.write_text(invalid)
            with self.assertRaises(bootstrap.BootstrapError):
                bootstrap.validate_config(config, str(root))
        config.write_text(expected)
        shutil.rmtree(root / "src")
        with self.assertRaises(bootstrap.BootstrapError):
            bootstrap.validate_config(config, str(root))

    def test_prepare_selects_after_fragments_and_rechecks_after_defconfig(self):
        root = self.make_root()
        project = self.work / "project"
        for directory in ("scripts", "config/rc01/files/etc/board.d", "config/rc01/files/etc/uci-defaults",
                          "defconfig", "feeds/nikki/mihomo-meta", "test-bin"):
            (project / directory).mkdir(parents=True, exist_ok=True)
        for name in ("prepare-rc01.sh", "rc01-go-bootstrap.py", "kconfig.pl"):
            shutil.copy2(ROOT / "scripts" / name, project / "scripts" / name)
        (project / "config/rc01/feeds.conf").write_text(f"src-git packages local^{FEED_REVISION}\n")
        for name in ("board.d/99-rc01-wireless", "uci-defaults/89-rc01-bypass"):
            (project / "config/rc01/files/etc" / name).write_text("fixture\n")
        stale = 'CONFIG_GOLANG_BUILD_BOOTSTRAP=y\nCONFIG_GOLANG_EXTERNAL_BOOTSTRAP_ROOT="/stale"\n'
        (project / "defconfig/qualcommbe-rc01.config").write_text(stale)
        (project / "config/rc01/dae.config").write_text(stale + "CONFIG_FRAGMENT_APPLIED=y\n")
        (project / "feeds/nikki/mihomo-meta/Makefile").write_text('PKG_BUILD_TIME:=$(shell date -u -Iseconds)\n')

        def executable(name, content):
            path = project / name
            path.write_text("#!/bin/sh\nset -eu\n" + content)
            path.chmod(0o755)

        executable("scripts/feeds", ":\n")
        # Source resolution is an independent collaborator here; this test
        # checks bootstrap ordering against prepare's real shell control flow.
        (project / "scripts/rc01-software-sources.py").write_text(
            "from pathlib import Path\nimport shutil, sys\n"
            "if sys.argv[1] == 'plan':\n"
            "    Path('.rc01-sources').mkdir(exist_ok=True)\n"
            "    Path('.rc01-sources/lock.json').write_text('{\"mode\":\"locked\",\"source_date_epoch\":1}')\n"
            "    shutil.copyfile('config/rc01/feeds.conf', '.rc01-sources/feeds.conf')\n"
        )
        for name in ("golang", "daede"):
            executable(f"scripts/patch-rc01-{name}.sh", f'echo patch-{name} >> "$RC01_TEST_EVENTS"\n')
        (project / "scripts/patch-rc01-packages.py").write_text(
            "import os\nfrom pathlib import Path\n"
            "with Path(os.environ['RC01_TEST_EVENTS']).open('a') as events:\n"
            "    events.write('patch-packages\\n')\n"
        )
        executable("scripts/validate-rc01-config.sh",
                   'exec "$RC01_TEST_PYTHON" -B scripts/rc01-go-bootstrap.py validate-config "$1"\n')
        executable("test-bin/python3", 'exec "$RC01_TEST_PYTHON" -B "$@"\n')
        executable("test-bin/git", f'echo feed-head >> "$RC01_TEST_EVENTS"\nprintf "%s\\n" {FEED_REVISION}\n')
        executable("test-bin/make", '''case "$*" in
    defconfig)
        grep -Fqx CONFIG_FRAGMENT_APPLIED=y .config
        "$RC01_TEST_PYTHON" -B scripts/rc01-go-bootstrap.py validate-config .config
        echo defconfig >> "$RC01_TEST_EVENTS"
        if [ "${RC01_TEST_MANGLE_CONFIG:-0}" = 1 ]; then
            echo CONFIG_GOLANG_BUILD_BOOTSTRAP=y >> .config
        fi ;;
    *) echo recipe-parse >> "$RC01_TEST_EVENTS" ;;
esac
''')
        events = project / "events"
        env = os.environ | {"PATH": str(project / "test-bin") + os.pathsep + os.environ["PATH"],
                            "RC01_TEST_PYTHON": sys.executable, "RC01_TEST_EVENTS": str(events),
                            "RC01_WITH_DAE": "true"}

        def prepare(extra):
            events.unlink(missing_ok=True)
            return subprocess.run(["sh", "scripts/prepare-rc01.sh"], cwd=project,
                                  env=env | extra, capture_output=True, text=True)

        for selected in (str(root), ""):
            with self.subTest(selected=selected):
                result = prepare({"RC01_GO_BOOTSTRAP_ROOT": selected})
                self.assertEqual(result.returncode, 0, result.stderr)
                bootstrap.validate_config(project / ".config", selected)
                self.assertEqual(events.read_text().splitlines(),
                                 ["patch-daede", "feed-head", "patch-golang", "patch-packages", "defconfig", "recipe-parse"])
        result = prepare({"RC01_GO_BOOTSTRAP_ROOT": str(root), "RC01_TEST_MANGLE_CONFIG": "1"})
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("recipe-parse", events.read_text())
        result = prepare({"RC01_GO_BOOTSTRAP_ROOT": str(root), "RC01_TEST_VERSION": "wrong"})
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("defconfig", events.read_text())


class CompiledGoTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="rc01-compiled-go-")
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.log = self.work / "go-invocations"
        self.environment = mock.patch.dict(os.environ, {
            "RC01_GO_TEST_LOG": str(self.log), "GOTOOLCHAIN": "auto",
            "GOENV": "/untrusted/goenv", "GOROOT": "/untrusted/root",
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.lock = {"schema": 1, "prepared": True, "mode": "latest", "inputs": {},
                     "go": {"version": "1.27.4", "bootstrap": "1.24.13"}}
        for name in (compiler.COMPILER, compiler.BOOTSTRAP, compiler.VALUES):
            path = self.work / name
            path.parent.mkdir(parents=True, exist_ok=True)
            baseline = {
                compiler.COMPILER: "golang1.27.Makefile",
                compiler.BOOTSTRAP: "golang-bootstrap.Makefile",
                compiler.VALUES: "inputs/go/lang/golang/golang-values.mk",
            }[name]
            source = (ROOT / "config/rc01/software-baselines" / baseline).read_text()
            if name == compiler.COMPILER:
                source = re.sub(r"^GO_VERSION_PATCH:=.*$", "GO_VERSION_PATCH:=4", source, flags=re.M)
            path.write_text(source)
            self.lock["inputs"][name] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.lock["go"]["recipe_sha256"] = self.lock["inputs"][compiler.COMPILER]
        binary = self.work / compiler.BINARY
        binary.parent.mkdir(parents=True)
        binary.write_bytes(GO_FIXTURE.replace(("go" + bootstrap.VERSION).encode(), b"go1.27.4"))
        binary.chmod(0o755)

    def test_valid_new_patch_release_is_checked_against_real_command_and_lock(self):
        self.assertEqual(compiler.check(self.work, self.lock), "go version go1.27.4 linux/amd64")
        self.assertEqual(self.log.read_text().splitlines(), [
            "local|off|unset|version", "local|off|unset|env GOROOT GOHOSTOS GOHOSTARCH",
        ])
        lock = self.work / ".rc01-sources/lock.json"
        lock.parent.mkdir()
        lock.write_text(json.dumps(self.lock))
        result = subprocess.run([sys.executable, "-B", str(GO_VALIDATOR), "--root", str(self.work)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "go version go1.27.4 linux/amd64\n")
        # The locked baseline must use the same acceptance path, not a fixed
        # compiler version hidden in a separate CI branch.
        self.lock["mode"] = "locked"
        self.assertEqual(compiler.check(self.work, self.lock), "go version go1.27.4 linux/amd64")

    def test_different_actual_version_host_or_root_is_rejected(self):
        variants = ({"RC01_TEST_VERSION": "go version go1.27.3 linux/amd64"},
                    {"RC01_TEST_VERSION": "go version go1.27.4 linux/arm64"},
                    {"RC01_TEST_HOST_OS": "darwin"}, {"RC01_TEST_HOST_ARCH": "arm64"},
                    {"RC01_TEST_REPORTED_ROOT": str(self.work)})
        for environment in variants:
            with self.subTest(environment=environment), mock.patch.dict(os.environ, environment):
                with self.assertRaises(compiler.GoVersionError):
                    compiler.check(self.work, self.lock)

    def test_incomplete_lock_or_unreviewed_chain_never_executes_go(self):
        variants = []
        for key, value in (("prepared", False), ("mode", "untrusted"), ("schema", 2)):
            lock = json.loads(json.dumps(self.lock)); lock[key] = value; variants.append(lock)
        for key, value in (("version", "1.28.0"), ("version", "1.27.4rc1"),
                           ("bootstrap", "1.25.0"), ("recipe_sha256", "0" * 64)):
            lock = json.loads(json.dumps(self.lock)); lock["go"][key] = value; variants.append(lock)
        lock = json.loads(json.dumps(self.lock)); lock["inputs"].pop(compiler.BOOTSTRAP); variants.append(lock)
        for lock in variants:
            with self.subTest(lock=lock), self.assertRaises(compiler.GoVersionError):
                compiler.check(self.work, lock)
        self.assertFalse(self.log.exists())

    def test_recipe_content_and_version_provenance_drift_is_rejected(self):
        recipe = self.work / compiler.COMPILER
        original = recipe.read_text()
        recipe.write_text(original + "# content drift\n")
        with self.assertRaises(compiler.GoVersionError):
            compiler.check(self.work, self.lock)
        # A lock version cannot disagree with even a matching recipe hash.
        recipe.write_text(original.replace("GO_VERSION_PATCH:=4", "GO_VERSION_PATCH:=5"))
        self.lock["inputs"][compiler.COMPILER] = hashlib.sha256(recipe.read_bytes()).hexdigest()
        self.lock["go"]["recipe_sha256"] = self.lock["inputs"][compiler.COMPILER]
        with self.assertRaises(compiler.GoVersionError):
            compiler.check(self.work, self.lock)
        self.assertFalse(self.log.exists())

    def test_workflow_uses_validator_and_publishes_only_generic_repository_identity(self):
        workflow = (ROOT / ".github/workflows/build-rc01.yml").read_text()
        self.assertEqual(workflow.count("python3 -B scripts/validate-rc01-go-version.py"), 2)
        self.assertNotIn("go version go1.27.3 linux/amd64", workflow)
        self.assertIn('gh run download "$REPLAY_RUN_ID" --repo "$GITHUB_REPOSITORY"', workflow)
        lines = workflow.splitlines()
        identity = next(i for i, line in enumerate(lines) if 'echo "repository=' in line)
        command = "\n".join(line.strip() for line in lines[identity:identity + 3])
        result = subprocess.run(["sh", "-c", command], cwd=ROOT,
                                env=os.environ | {"GITHUB_REPOSITORY": "private-fixture-owner/custom-name"},
                                capture_output=True, text=True, check=True)
        metadata = dict(line.split("=", 1) for line in result.stdout.splitlines())
        self.assertEqual(metadata["repository"], "immortalwrt-viking")
        self.assertRegex(metadata["source_commit"], r"^[0-9a-f]{40}$")
        self.assertNotIn("private-fixture-owner", result.stdout)


class FeedPatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.feed = Path(os.environ.get("RC01_PACKAGES_FEED", str(ROOT / "feeds/packages")))
        if not cls.feed.is_dir():
            raise unittest.SkipTest("packages feed is not prepared; real patch tests run after source preparation")
        top = subprocess.run(["git", "-C", str(cls.feed), "rev-parse", "--show-toplevel"], capture_output=True, text=True)
        if top.returncode or Path(top.stdout.strip()).resolve() != cls.feed.resolve():
            raise RuntimeError("existing packages directory is not an independent Git feed")
        head = subprocess.run(["git", "-C", str(cls.feed), "rev-parse", "HEAD"], capture_output=True, text=True)
        if head.returncode:
            raise RuntimeError("existing packages feed has no readable HEAD")
        before = head.stdout.strip()
        def unchanged():
            after = subprocess.check_output(["git", "-C", str(cls.feed), "rev-parse", "HEAD"], text=True).strip()
            if after != before:
                raise AssertionError("shared packages feed HEAD changed during disposable patch tests")
        cls.addClassCleanup(unchanged)
        cls.originals = {}
        for name in ("golang-compiler.mk", "golang-host-build.mk", "golang-values.mk"):
            result = subprocess.run(["git", "-C", str(cls.feed), "show", f"{FEED_REVISION}:lang/golang/{name}"],
                                    capture_output=True, text=True)
            if result.returncode:
                raise unittest.SkipTest("pinned packages feed objects are required for the real patch tests")
            cls.originals[name] = result.stdout

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="rc01-go-patch-")
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.checkout = self.work / "packages"
        # No checkout/branch change in the shared feed. Reuse its local objects
        # in a disposable repository and materialize only the files under test.
        subprocess.run(["git", "clone", "--quiet", "--shared", "--no-checkout", str(self.feed), str(self.checkout)], check=True)
        # The feed under build may be newer. These exact baseline patch tests
        # only detach their private clone at the already-verified old object.
        subprocess.run(["git", "-C", str(self.checkout), "update-ref", "--no-deref", "HEAD", FEED_REVISION], check=True)
        self.assertEqual(subprocess.check_output(["git", "-C", str(self.checkout), "rev-parse", "HEAD"], text=True).strip(), FEED_REVISION)
        self.golang = self.checkout / "lang/golang"
        self.golang.mkdir(parents=True)
        for name, text in self.originals.items():
            (self.golang / name).write_text(text)

    def apply(self, feed=None):
        return subprocess.run(["sh", str(PATCH_SCRIPT), str(feed or self.checkout)], capture_output=True, text=True)

    def test_real_patch_is_exact_idempotent_and_local_in_actual_make_calls(self):
        result = self.apply()
        self.assertEqual(result.returncode, 0, result.stderr)
        expected = dict(self.originals)
        expected["golang-compiler.mk"] = expected["golang-compiler.mk"].replace(
            "\t$(2) $(BASH) make.bash", "\t$(2) GOTOOLCHAIN=local $(BASH) make.bash")
        expected["golang-host-build.mk"] = expected["golang-host-build.mk"].replace(
            "\tGOENV=off\n", "\tGOENV=off \\\n\tGOTOOLCHAIN=local\n")
        for name, text in expected.items():
            self.assertEqual((self.golang / name).read_text(), text)
        self.assertEqual(self.apply().returncode, 0)
        subprocess.run(["git", "-C", str(self.checkout), "apply", "--reverse", "--check", str(PATCH)], check=True)

        source = self.work / "compiler/src"
        source.mkdir(parents=True)
        (source / "make.bash").write_text(
            'printf "compiler|%s|%s|%s\\n" "$GOTOOLCHAIN" "$GOPROXY" "$GOSUMDB" >> "$RC01_TEST_CALLS"\n')
        (self.golang / "golang-build.sh").write_text(
            'printf "host|%s|%s|%s|%s\\n" "$GOTOOLCHAIN" "$GOENV" "$GOPROXY" "$GOSUMDB" >> "$RC01_TEST_CALLS"\n')
        makefile = self.work / "Makefile"
        makefile.write_text(f'''GO_INCLUDE_DIR:={self.golang}
BASH:=/bin/bash
include $(GO_INCLUDE_DIR)/golang-compiler.mk
include $(GO_INCLUDE_DIR)/golang-host-build.mk
.PHONY: all
all:
\t$(call GoCompiler/Default/Make,{source.parent},GOTOOLCHAIN=auto)
\t$(call GoHost/Host/Compile)
''')
        calls = self.work / "calls"
        env = os.environ | {"GOTOOLCHAIN": "auto", "GOENV": "/bad/goenv", "RC01_TEST_CALLS": str(calls),
                            "GOPROXY": "https://proxy.golang.org", "GOSUMDB": "sum.golang.org"}
        result = subprocess.run(["make", "--no-print-directory", "-f", str(makefile)],
                                cwd=self.work, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls.read_text().splitlines(), [
            "compiler|local|https://proxy.golang.org|sum.golang.org",
            "host|local|off|https://proxy.golang.org|sum.golang.org",
        ])

    def test_wrong_feed_is_rejected_without_changes(self):
        wrong = self.work / "wrong"
        subprocess.run(["git", "init", "--quiet", str(wrong)], check=True)
        # Use the main repository's real revision as an incorrect feed fixture;
        # this also works when the packages feed contains only a shallow commit.
        revision = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True)
        (wrong / ".git/objects/info/alternates").write_text(str(ROOT / ".git/objects") + "\n")
        (wrong / ".git/HEAD").write_text(revision)
        marker = wrong / "preserve"
        marker.write_text("unchanged")
        result = self.apply(wrong)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unexpected packages revision", result.stderr)
        self.assertEqual(marker.read_text(), "unchanged")
        self.assertEqual({path.name for path in wrong.iterdir()}, {".git", "preserve"})

    def test_changed_call_site_is_rejected_before_either_file_is_patched(self):
        compiler = self.golang / "golang-compiler.mk"
        compiler.write_text(compiler.read_text().replace("$(BASH) make.bash", "$(BASH) changed.bash"))
        before = {name: (self.golang / name).read_bytes() for name in self.originals}
        self.assertNotEqual(self.apply().returncode, 0)
        self.assertEqual({name: (self.golang / name).read_bytes() for name in self.originals}, before)


if __name__ == "__main__":
    unittest.main()
