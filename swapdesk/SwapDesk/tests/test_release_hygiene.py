"""Release hygiene: version consistency, layout, no AI/LLM, no committed secrets.

These guard the shape of the repository rather than swap behaviour. They read
files only (no network, no display) and run from the SwapDesk/ folder or the
repo root alike.
"""
import re
import subprocess
import sys
import unittest
from pathlib import Path

import build

APP = Path(__file__).resolve().parents[1]
REPO = APP.parent
SKIP_DIRS = {".venv", "venv", "build", "dist", "__pycache__", ".git", "data"}

SEMVER = re.compile(r"^\d+\.\d+\.\d+(-[0-9A-Za-z.]+)?$")

# Package/import names that would mean an AI or LLM integration. Matched as
# whole dotted components, so e.g. "openai" does not match "openai-compat" by
# accident of substring in unrelated words.
AI_NAMES = {
    "openai", "anthropic", "google-generativeai", "google.generativeai",
    "genai", "langchain", "llama-index", "llama_index", "llamaindex",
    "transformers", "torch", "tensorflow", "huggingface-hub", "huggingface_hub",
    "ollama", "cohere", "mistralai", "litellm", "tiktoken", "sentence-transformers",
    "sentence_transformers", "onnxruntime", "gpt4all", "llama-cpp-python",
    "llama_cpp", "vllm", "replicate", "groq",
}

SECRET_PATTERNS = [
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"xox[abprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"sk-[A-Za-z0-9]{32,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}\."),
]
PERSONAL_PATH = re.compile(r"(/home/[a-z0-9_.-]+/|/Users/[A-Za-z0-9_.-]+/|C:\\Users\\[A-Za-z0-9_.-]+\\)")


def _repo_files(root, suffixes=None):
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        if suffixes is None or path.suffix in suffixes or path.name in suffixes:
            yield path


def _text(path):
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None


class VersionConsistency(unittest.TestCase):
    def test_version_file_is_a_single_clean_semver(self):
        raw = (APP / "VERSION").read_text(encoding="utf-8")
        self.assertEqual(raw, raw.strip() + "\n")
        self.assertRegex(raw.strip(), SEMVER)

    def test_build_reads_the_same_version(self):
        version = (APP / "VERSION").read_text(encoding="utf-8").strip()
        self.assertEqual(build.version_string(), version)
        core = [int(x) for x in version.split("-")[0].split(".")]
        self.assertEqual(build.version_tuple(), (*core, 0))

    def test_readme_and_changelog_name_the_current_version(self):
        version = (APP / "VERSION").read_text(encoding="utf-8").strip()
        readme = (REPO / "README.md").read_text(encoding="utf-8")
        self.assertIn(f"v{version}", readme.splitlines()[2])
        changelog = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertRegex(changelog, rf"\*\*v{re.escape(version)}\b")

    def test_preflight_cli_reports_the_version(self):
        version = (APP / "VERSION").read_text(encoding="utf-8").strip()
        out = subprocess.run(
            [sys.executable, str(APP / "preflight.py"), "--version"],
            capture_output=True, text=True, check=True, timeout=60, cwd=APP)
        self.assertIn(version, out.stdout + out.stderr)

    def test_no_other_version_literals_in_active_metadata(self):
        version = (APP / "VERSION").read_text(encoding="utf-8").strip()
        for rel in (".github/workflows/ci.yml", "SwapDesk/build.py"):
            # Comments may cite the release a change landed in; only code and
            # config lines are active metadata.
            text = "\n".join(
                line for line in (REPO / rel).read_text(encoding="utf-8").splitlines()
                if not line.lstrip().startswith("#"))
            for found in re.findall(r"\b0\.1\.\d+(?:-alpha)?\b", text):
                self.assertEqual(found, version, f"{rel} hard-codes {found}")


class RepositoryLayout(unittest.TestCase):
    def test_single_canonical_root(self):
        for name in ("README.md", "CHANGELOG.md", "SECURITY.md", "CONTRIBUTING.md",
                     "LICENSE", ".gitignore", "requirements-build.txt",
                     ".github/workflows/ci.yml"):
            self.assertTrue((REPO / name).is_file(), f"missing {name}")
        for dup in ("README.md", "CHANGELOG.md", "SECURITY.md", "LICENSE"):
            self.assertFalse((APP / dup).exists(), f"duplicate {dup} inside SwapDesk/")
        self.assertFalse((APP / "docs" / "CHANGELOG.md").exists())
        self.assertFalse((APP / "requirements-build.txt").exists())
        nested = [p for p in REPO.iterdir() if p.is_dir() and p.name.lower().startswith("swapdesk-")]
        self.assertEqual(nested, [])

    def test_build_resources_resolve(self):
        for rel in build.PACKAGE_DOCS:
            self.assertTrue((APP / rel).exists() or (REPO / rel).exists(), rel)
        self.assertTrue((APP / "requirements.txt").is_file())

    def test_build_scripts_point_at_root_build_requirements(self):
        for rel in ("linux/build_linux.sh", "macos/build_macos.command",
                    "windows/build_windows.bat"):
            text = (APP / "scripts" / rel).read_text(encoding="utf-8")
            self.assertRegex(text, r"-r \.\.[/\\]requirements-build\.txt", rel)

    def test_ci_working_directory_exists(self):
        ci = (REPO / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        for rel in re.findall(r"working-directory:\s*(\S+)", ci):
            self.assertTrue((REPO / rel).is_dir(), rel)
        for rel in re.findall(r"cache-dependency-path:\s*(\S+)", ci):
            self.assertTrue((REPO / rel).is_file(), rel)

    def test_markdown_relative_links_resolve(self):
        link = re.compile(r"\]\((?!https?:|mailto:|#)([^)#\s]+)")
        for md in _repo_files(REPO, {".md"}):
            for target in link.findall(md.read_text(encoding="utf-8")):
                self.assertTrue((md.parent / target).exists(), f"{md.name}: {target}")

    def test_dependencies_stay_hash_pinned(self):
        for path in (APP / "requirements.txt", REPO / "requirements-build.txt"):
            text = path.read_text(encoding="utf-8")
            for block in re.split(r"\n(?=[A-Za-z0-9_.-]+==)", text):
                if re.match(r"[A-Za-z0-9_.-]+==", block):
                    self.assertIn("--hash=sha256:", block, block.split()[0])


class LauncherScripts(unittest.TestCase):
    POSIX_SCRIPTS = ("dev/swapdesk.sh", "dev/swapdesk-debug.sh", "dev/SwapDesk.command",
                     "linux/build_linux.sh", "macos/build_macos.command")

    def test_posix_launchers_have_a_shebang(self):
        for rel in self.POSIX_SCRIPTS:
            text = (APP / "scripts" / rel).read_text(encoding="utf-8")
            self.assertTrue(text.startswith("#!"), rel)

    @unittest.skipIf(sys.platform == "win32", "no POSIX mode bits on Windows")
    def test_posix_launchers_are_executable(self):
        # The README tells macOS users to double-click SwapDesk.command and
        # Linux users to run ./scripts/dev/swapdesk.sh; both fail without +x,
        # and SwapDesk.command execs swapdesk.sh directly.
        import os
        for rel in self.POSIX_SCRIPTS:
            self.assertTrue(os.access(APP / "scripts" / rel, os.X_OK), f"{rel} is not +x")

    def test_gitignore_covers_files_the_app_writes_beside_itself(self):
        ignore = (REPO / ".gitignore").read_text(encoding="utf-8").split()
        for name in ("SwapDesk/data/", "encryption_enabled", "permission_warning.txt",
                     "*.log"):
            self.assertIn(name, ignore)


class NoAiOrLlm(unittest.TestCase):
    def test_requirements_have_no_ai_packages(self):
        for path in (APP / "requirements.txt", REPO / "requirements-build.txt"):
            names = {m.lower().replace("_", "-") for m in
                     re.findall(r"^([A-Za-z0-9_.-]+)==", path.read_text(encoding="utf-8"), re.M)}
            self.assertFalse(names & {n.replace("_", "-") for n in AI_NAMES}, path.name)

    def test_source_has_no_ai_imports(self):
        imp = re.compile(r"^\s*(?:from|import)\s+([A-Za-z0-9_.]+)", re.M)
        for path in _repo_files(APP, {".py"}):
            for module in imp.findall(path.read_text(encoding="utf-8")):
                top = module.split(".")[0].lower()
                self.assertNotIn(top, {n.split(".")[0] for n in AI_NAMES},
                                 f"{path.name} imports {module}")

    def test_no_ai_provider_endpoints_or_env_vars(self):
        hosts = re.compile(r"api\.openai\.com|api\.anthropic\.com|generativelanguage\.googleapis\.com"
                           r"|api\.mistral\.ai|api\.cohere\.(?:ai|com)|api\.groq\.com", re.I)
        envs = re.compile(r"\b(?:OPENAI|ANTHROPIC|GEMINI|MISTRAL|COHERE|GROQ)_API_KEY\b")
        for path in _repo_files(REPO):
            text = _text(path)
            if text is None or path.name == "test_release_hygiene.py":
                continue
            self.assertIsNone(hosts.search(text), path.name)
            self.assertIsNone(envs.search(text), path.name)


class NoLeaks(unittest.TestCase):
    def test_no_local_config_or_user_data_in_tree(self):
        banned = {"config.json", "config.enc", "history.json", "address_book.json",
                  ".env", "keys.local.json", "bundled_keys.py", "remote.py"}
        for path in _repo_files(REPO):
            self.assertNotIn(path.name, banned, str(path.relative_to(REPO)))

    def test_no_secret_shaped_strings(self):
        for path in _repo_files(REPO):
            text = _text(path)
            if text is None or path.name == "test_release_hygiene.py":
                continue
            for pattern in SECRET_PATTERNS:
                self.assertIsNone(pattern.search(text),
                                  f"{path.relative_to(REPO)} matches {pattern.pattern[:20]}")

    def test_no_personal_absolute_paths(self):
        for path in _repo_files(REPO):
            text = _text(path)
            if text is None or path.name == "test_release_hygiene.py":
                continue
            self.assertIsNone(PERSONAL_PATH.search(text), str(path.relative_to(REPO)))

    def test_no_binary_assets_carry_metadata_risk(self):
        # The repo ships no artwork; an image or document appearing here needs a
        # deliberate metadata review (EXIF, PNG text chunks, PDF author).
        risky = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".pdf", ".ico",
                 ".icns", ".docx", ".xlsx", ".pptx", ".exe", ".dll", ".zip"}
        found = [str(p.relative_to(REPO)) for p in _repo_files(REPO) if p.suffix.lower() in risky]
        self.assertEqual(found, [], "new binary asset needs a metadata review")


if __name__ == "__main__":
    unittest.main()
