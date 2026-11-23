# SPDX-License-Identifier: GPL-2.0-only
"""Exercise bootstrap verification and the pinned Make calls without downloads."""

import hashlib
import importlib.util
import io
import os
from contextlib import redirect_stdout
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parent))
import prepare_fixture  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/rc01-go-bootstrap.py"
PATCH = ROOT / "config/rc01/patches/golang/100-rc01-local-toolchain.patch"
FIXTURES = ROOT / "tests/rc01/fixtures/go"
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
        stale = 'CONFIG_GOLANG_BUILD_BOOTSTRAP=y\nCONFIG_GOLANG_EXTERNAL_BOOTSTRAP_ROOT="/stale"\n'
        project = prepare_fixture.make_project(self.work, defconfig=stale)

        def prepare(extra):
            return prepare_fixture.run_prepare(project, sys.executable, extra)

        for selected in (str(root), ""):
            with self.subTest(selected=selected):
                result, events = prepare({"RC01_GO_BOOTSTRAP_ROOT": selected})
                self.assertEqual(result.returncode, 0, result.stderr)
                bootstrap.validate_config(project / ".config", selected)
                # Feeds and patches come first; the bootstrap is selected only
                # after the fragments, then re-checked after defconfig.
                steps = [event for event in events if event in ("feeds update -i", "defconfig", "validate", "recipe-parse")]
                self.assertEqual(steps, ["feeds update -i", "defconfig", "validate", "recipe-parse"])
        result, events = prepare({"RC01_GO_BOOTSTRAP_ROOT": str(root), "RC01_TEST_MANGLE_CONFIG": "1"})
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("recipe-parse", events)
        result, events = prepare({"RC01_GO_BOOTSTRAP_ROOT": str(root), "RC01_TEST_VERSION": "wrong"})
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("defconfig", events)


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
        # The recipes of the pinned packages feed select the expected compiler.
        for name, fixture in ((compiler.COMPILER, "golang1.27/Makefile"),
                              (compiler.BOOTSTRAP, "golang-bootstrap/Makefile"),
                              (compiler.VALUES, "golang-values.mk")):
            path = self.work / name
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(FIXTURES / "lang/golang" / fixture, path)
        binary = self.work / compiler.BINARY
        binary.parent.mkdir(parents=True)
        binary.write_bytes(GO_FIXTURE.replace(("go" + bootstrap.VERSION).encode(), b"go1.27.4"))
        binary.chmod(0o755)

    def test_built_compiler_is_checked_against_real_command_and_pinned_recipe(self):
        self.assertEqual(compiler.check(self.work), "go version go1.27.4 linux/amd64")
        self.assertEqual(self.log.read_text().splitlines(), [
            "local|off|unset|version", "local|off|unset|env GOROOT GOHOSTOS GOHOSTARCH",
        ])
        result = subprocess.run([sys.executable, "-B", str(GO_VALIDATOR), "--root", str(self.work)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "go version go1.27.4 linux/amd64\n")

    def test_different_actual_version_host_or_root_is_rejected(self):
        variants = ({"RC01_TEST_VERSION": "go version go1.27.3 linux/amd64"},
                    {"RC01_TEST_VERSION": "go version go1.27.4 linux/arm64"},
                    {"RC01_TEST_HOST_OS": "darwin"}, {"RC01_TEST_HOST_ARCH": "arm64"},
                    {"RC01_TEST_REPORTED_ROOT": str(self.work)})
        for environment in variants:
            with self.subTest(environment=environment), mock.patch.dict(os.environ, environment):
                with self.assertRaises(compiler.GoVersionError):
                    compiler.check(self.work)

    def test_unreviewed_recipe_chain_never_executes_go(self):
        recipe = self.work / compiler.COMPILER
        original = recipe.read_text()
        for old, new in (("GO_VERSION_MAJOR_MINOR:=1.27", "GO_VERSION_MAJOR_MINOR:=1.28"),
                         ("GO_VERSION_PATCH:=4", "GO_VERSION_PATCH:=4rc1"),
                         ("GO_VERSION_RC:=", "GO_VERSION_RC:=rc1"),
                         ("GO_BOOTSTRAP_VERSION:=bootstrap", "GO_BOOTSTRAP_VERSION:=1.24")):
            with self.subTest(change=new):
                self.assertIn(old, original)
                recipe.write_text(original.replace(old, new))
                with self.assertRaises(compiler.GoVersionError):
                    compiler.check(self.work)
        recipe.write_text(original)
        bootstrap_recipe = self.work / compiler.BOOTSTRAP
        bootstrap_recipe.write_text(bootstrap_recipe.read_text().replace("PATCH:=13", "PATCH:=12"))
        with self.assertRaises(compiler.GoVersionError):
            compiler.check(self.work)
        bootstrap_recipe.unlink()
        with self.assertRaises(compiler.GoVersionError):
            compiler.check(self.work)
        self.assertFalse(self.log.exists())

    def test_workflow_uses_validator_and_publishes_only_generic_repository_identity(self):
        workflow = (ROOT / ".github/workflows/build-rc01.yml").read_text()
        self.assertEqual(workflow.count("python3 -B scripts/validate-rc01-go-version.py"), 2)
        self.assertNotIn("go version go1.27.3 linux/amd64", workflow)
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


class LocalToolchainPatch(unittest.TestCase):
    """The Go patch on pristine copies of the pinned recipes, with real make calls."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="rc01-go-patch-")
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.checkout = self.work / "packages"
        self.golang = self.checkout / "lang/golang"
        self.golang.mkdir(parents=True)
        self.originals = {}
        for name in ("golang-compiler.mk", "golang-host-build.mk", "golang-values.mk"):
            self.originals[name] = (FIXTURES / "lang/golang" / name).read_text()
            (self.golang / name).write_text(self.originals[name])
        subprocess.run(["git", "init", "--quiet", str(self.checkout)], check=True)

    def git_apply(self, *arguments):
        return subprocess.run(["git", "-C", str(self.checkout), "apply", *arguments, str(PATCH)],
                              capture_output=True, text=True)

    def test_patch_is_exact_and_reversible(self):
        self.assertEqual(self.git_apply("--check").returncode, 0)
        self.assertEqual(self.git_apply().returncode, 0)
        expected = dict(self.originals)
        expected["golang-compiler.mk"] = expected["golang-compiler.mk"].replace(
            "\t$(2) $(BASH) make.bash", "\t$(2) GOTOOLCHAIN=local $(BASH) make.bash")
        expected["golang-host-build.mk"] = expected["golang-host-build.mk"].replace(
            "\tGOENV=off\n", "\tGOENV=off \\\n\tGOTOOLCHAIN=local\n")
        for name, text in expected.items():
            self.assertEqual((self.golang / name).read_text(), text)
        self.assertEqual(self.git_apply("--reverse", "--check").returncode, 0)
        # Applied twice is refused, which is why prepare-rc01.sh tests the inverse first.
        self.assertNotEqual(self.git_apply("--check").returncode, 0)

    def test_local_toolchain_in_actual_make_calls(self):
        self.assertEqual(self.git_apply().returncode, 0)
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

    def test_changed_call_site_fails_the_check_and_leaves_both_files_unpatched(self):
        compiler_mk = self.golang / "golang-compiler.mk"
        compiler_mk.write_text(compiler_mk.read_text().replace("$(BASH) make.bash", "$(BASH) changed.bash"))
        before = {name: (self.golang / name).read_bytes() for name in self.originals}
        self.assertNotEqual(self.git_apply("--check").returncode, 0)
        self.assertNotEqual(self.git_apply().returncode, 0)
        self.assertEqual({name: (self.golang / name).read_bytes() for name in self.originals}, before)


if __name__ == "__main__":
    unittest.main()
