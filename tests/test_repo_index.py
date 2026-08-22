"""Repository intelligence (SPEC §32).

The fixture repos are deliberately modelled on the SPEC's own worked examples:
`UserRepository` exists but has no `get_active_users` (§14), and a pagination utility
already exists (§33). Later phases assert against the same fixtures.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

import pytest

from agentguard.repo import RepoIndex

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def pyrepo(tmp_path) -> Path:
    dest = tmp_path / "pyrepo"
    shutil.copytree(
        FIXTURES / "pyrepo",
        dest,
        ignore=shutil.ignore_patterns("__pycache__", "*.py[cod]"),
    )
    return dest


@pytest.fixture
def jsrepo(tmp_path) -> Path:
    dest = tmp_path / "jsrepo"
    shutil.copytree(
        FIXTURES / "jsrepo",
        dest,
        ignore=shutil.ignore_patterns("__pycache__", "*.py[cod]"),
    )
    return dest


@pytest.fixture
def git_pyrepo(pyrepo) -> Path:
    """Same fixture under git, to exercise the `git ls-files` fast path."""
    subprocess.run(["git", "init", "-q"], cwd=pyrepo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=pyrepo, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"],
        cwd=pyrepo,
        check=True,
    )
    return pyrepo


@pytest.fixture
def index(pyrepo) -> RepoIndex:
    return RepoIndex(pyrepo).build()


class TestScanning:
    def test_finds_source_test_and_config_files(self, index):
        assert index.file_exists("src/shop/repositories/user.py")
        assert index.files["tests/test_pagination.py"].is_test
        assert index.files["pyproject.toml"].is_config
        assert not index.files["src/shop/models.py"].is_test

    def test_detects_languages(self, index, jsrepo):
        assert index.files["src/shop/models.py"].lang == "python"
        js = RepoIndex(jsrepo).build()
        assert js.files["src/models.ts"].lang == "typescript"

    def test_git_and_walk_paths_agree(self, pyrepo, git_pyrepo):
        """The fast path and the fallback must see the same repository."""
        walked = RepoIndex(pyrepo).build()
        from_git = RepoIndex(git_pyrepo).build()
        assert set(walked.files) == set(from_git.files)

    def test_ignores_excluded_directories(self, pyrepo):
        junk = pyrepo / "node_modules" / "left-pad"
        junk.mkdir(parents=True)
        (junk / "index.js").write_text("module.exports = 1;")
        (pyrepo / "src" / "shop" / "__pycache__").mkdir()
        (pyrepo / "src" / "shop" / "__pycache__" / "models.cpython-312.pyc").write_bytes(b"\x00")

        index = RepoIndex(pyrepo).build()
        assert not any("node_modules" in path for path in index.files)
        assert not any("__pycache__" in path for path in index.files)

    def test_skips_oversized_files(self, pyrepo):
        (pyrepo / "src" / "huge.py").write_text("x = 1\n" * 500_000)
        from agentguard.core.config import IndexSettings

        index = RepoIndex(pyrepo, IndexSettings(max_file_bytes=1000)).build()
        assert "src/huge.py" not in index.files


class TestPythonSymbols:
    def test_extracts_classes_functions_and_methods(self, index):
        repo_class = index.find_symbol("UserRepository")
        assert len(repo_class) == 1
        assert repo_class[0].kind == "class"
        assert repo_class[0].path == "src/shop/repositories/user.py"

        method = index.find_symbol("UserRepository.get_by_id")
        assert method and method[0].kind == "method"
        assert "user_id" in method[0].signature

    def test_extracts_module_level_constants(self, index):
        constant = index.find_symbol("MAX_PAGE_SIZE")
        assert constant and constant[0].kind == "constant"

    def test_knows_a_types_attributes(self, index):
        attrs = index.attributes_of("UserRepository")
        assert {"get_by_id", "list_all", "count"} <= attrs

    def test_the_spec_14_absence_holds(self, index):
        """SPEC §14's example depends on this method genuinely not existing."""
        assert not index.has_symbol("get_active_users")
        assert "get_active_users" not in index.attributes_of("UserRepository")
        # But the type itself is known, which is what makes the challenge legitimate.
        assert index.knows_type("UserRepository")

    def test_unparsable_source_does_not_break_the_index(self, pyrepo):
        (pyrepo / "src" / "shop" / "broken.py").write_text("def oops(:\n    pass\n")
        index = RepoIndex(pyrepo).build()
        assert "src/shop/broken.py" in index.files
        assert index.has_symbol("UserRepository")  # the rest still indexed

    def test_a_failed_parse_is_distinguishable_from_an_empty_file(self, pyrepo):
        """The distinction that stops absence-of-evidence becoming evidence-of-absence.

        Both files yield zero symbols. Only the empty one was actually *read*, so only
        it licenses concluding that a symbol is missing (SPEC §14).
        """
        (pyrepo / "src" / "shop" / "broken.py").write_text("def oops(:\n    pass\n")
        (pyrepo / "src" / "shop" / "empty.py").write_text("")
        index = RepoIndex(pyrepo).build()

        assert index.symbols_in("src/shop/broken.py") == []
        assert index.symbols_in("src/shop/empty.py") == []
        assert index.is_parsed("src/shop/broken.py") is False
        assert index.is_parsed("src/shop/empty.py") is True

    def test_symbols_survive_a_partially_broken_file(self, jsrepo):
        """Positive evidence is always safe: a symbol that is there, is there."""
        target = jsrepo / "src" / "utils" / "pagination.ts"
        target.write_text(target.read_text() + "\nexport function later() { return ;;; @@@ }\n")
        index = RepoIndex(jsrepo).build()

        assert index.has_symbol("paginate"), "valid symbols must survive a broken tail"
        assert index.is_parsed("src/utils/pagination.ts") is False, "but the file is not trusted"


class TestTypeScriptSymbols:
    def test_extracts_classes_methods_interfaces_and_functions(self, jsrepo):
        index = RepoIndex(jsrepo).build()
        assert index.find_symbol("UserRepository")[0].kind == "class"
        assert {"getById", "listAll"} <= index.attributes_of("UserRepository")
        assert index.find_symbol("User")[0].kind == "interface"
        assert index.find_symbol("paginate")[0].kind == "function"


class TestImportGraph:
    def test_resolves_absolute_python_imports(self, index):
        assert index.resolve_module("shop.models") == "src/shop/models.py"
        assert index.resolve_module("shop.utils.pagination") == "src/shop/utils/pagination.py"

    def test_builds_reverse_dependencies(self, index):
        dependents = index.dependents_of("src/shop/models.py")
        assert "src/shop/repositories/user.py" in dependents
        assert "src/shop/utils/pagination.py" in dependents

    def test_blast_radius_is_transitive(self, index):
        """SPEC §12's blast-radius signal. models.py -> user.py -> users.py"""
        direct = index.dependents_of("src/shop/models.py")
        assert "src/shop/api/users.py" not in direct

        radius = index.blast_radius("src/shop/models.py", depth=3)
        assert "src/shop/api/users.py" in radius, "transitive dependents must be found"
        assert "src/shop/models.py" not in radius, "a file is not its own blast radius"

    def test_blast_radius_respects_depth(self, index):
        assert index.blast_radius("src/shop/models.py", depth=1) < index.blast_radius(
            "src/shop/models.py", depth=3
        )

    def test_resolves_relative_typescript_imports(self, jsrepo):
        index = RepoIndex(jsrepo).build()
        assert "src/repositories/userRepo.ts" in index.dependents_of("src/models.ts")
        assert "src/api/users.ts" in index.dependents_of("src/repositories/userRepo.ts")

    def test_external_imports_are_not_resolved_internally(self, jsrepo):
        index = RepoIndex(jsrepo).build()
        for record in index.imports_of("src/models.ts"):
            if record.raw == "react":
                assert record.resolved is None


class TestDependencies:
    def test_reads_pyproject(self, index):
        assert index.is_declared_dependency("fastapi")
        assert index.is_declared_dependency("sqlalchemy")
        assert index.is_declared_dependency("redis")  # optional-dependencies count
        assert index.is_declared_dependency("pytest")  # dependency-groups count
        assert not index.is_declared_dependency("django")

    def test_normalises_name_punctuation(self, index):
        index.dependencies.runtime["python-dateutil"] = ">=2.0"
        assert index.is_declared_dependency("python_dateutil")

    def test_reads_package_json(self, jsrepo):
        index = RepoIndex(jsrepo).build()
        assert index.is_declared_dependency("react")
        assert index.is_declared_dependency("vitest")
        assert not index.is_declared_dependency("angular")

    def test_a_broken_manifest_is_survivable(self, pyrepo):
        (pyrepo / "pyproject.toml").write_text("[project\nbroken =")
        index = RepoIndex(pyrepo).build()
        assert index.has_symbol("UserRepository")
        assert not index.is_declared_dependency("fastapi")


class TestTestMap:
    def test_maps_sources_to_their_tests(self, index):
        assert "tests/test_user_repository.py" in index.tests_for("src/shop/repositories/user.py")
        assert "tests/test_pagination.py" in index.tests_for("src/shop/utils/pagination.py")

    def test_knows_when_a_source_is_untested(self, index):
        """SPEC §19: the Completion Gate needs to know what is *not* covered."""
        assert not index.has_tests("src/shop/api/orders.py")

    def test_maps_colocated_typescript_tests(self, jsrepo):
        index = RepoIndex(jsrepo).build()
        assert "src/api/users.test.ts" in index.tests_for("src/api/users.ts")


class TestGit:
    def test_reports_no_repo_outside_git(self, index):
        assert index.git.is_repo is False

    def test_reads_branch_head_and_dirty_state(self, git_pyrepo):
        index = RepoIndex(git_pyrepo).build()
        state = index.git
        assert state.is_repo
        assert state.branch
        assert len(state.head) == 40
        assert state.recent_commits

        (git_pyrepo / "src" / "shop" / "models.py").write_text("# edited\n")
        fresh = RepoIndex(git_pyrepo)
        assert "src/shop/models.py" in fresh.git.dirty

    def test_reports_both_sides_of_a_git_rename(self, git_pyrepo):
        old = git_pyrepo / "src" / "shop" / "api" / "orders.py"
        new = git_pyrepo / "src" / "shop" / "api" / "purchases.py"
        old.rename(new)

        state = RepoIndex(git_pyrepo).git

        assert "src/shop/api/purchases.py" in state.dirty
        assert "src/shop/api/orders.py" in state.dirty


class TestIncrementalRefresh:
    def test_detects_a_modified_file(self, pyrepo, index):
        target = pyrepo / "src" / "shop" / "repositories" / "user.py"
        target.write_text(target.read_text() + "\n    def get_active_users(self):\n        return []\n")

        counts = index.refresh()
        assert counts["modified"] == 1
        assert index.has_symbol("get_active_users")
        assert "get_active_users" in index.attributes_of("UserRepository")

    def test_detects_an_added_file(self, pyrepo, index):
        (pyrepo / "src" / "shop" / "billing.py").write_text("def charge(amount):\n    return amount\n")
        counts = index.refresh()
        assert counts["added"] == 1
        assert index.has_symbol("charge")

    def test_detects_a_deleted_file(self, pyrepo, index):
        assert index.has_symbol("list_orders")
        (pyrepo / "src" / "shop" / "api" / "orders.py").unlink()
        counts = index.refresh()
        assert counts["removed"] == 1
        assert not index.has_symbol("list_orders")

    def test_detects_a_renamed_file(self, pyrepo, index):
        src = pyrepo / "src" / "shop" / "api" / "orders.py"
        src.rename(pyrepo / "src" / "shop" / "api" / "purchases.py")

        counts = index.refresh()
        assert counts == {"added": 1, "modified": 0, "removed": 1}
        assert not index.file_exists("src/shop/api/orders.py")
        assert index.file_exists("src/shop/api/purchases.py")
        assert index.find_symbol("list_orders")[0].path == "src/shop/api/purchases.py"

    def test_detects_a_renamed_symbol(self, pyrepo, index):
        """The SPEC §13 rename example, from the index's point of view."""
        target = pyrepo / "src" / "shop" / "api" / "users.py"
        target.write_text(target.read_text().replace("def get_user(", "def fetch_user("))

        index.refresh()
        assert not index.has_symbol("get_user")
        assert index.has_symbol("fetch_user")

    def test_rebuilds_the_dependency_graph_after_a_change(self, pyrepo, index):
        assert "src/shop/api/orders.py" in index.dependents_of("src/shop/models.py")
        (pyrepo / "src" / "shop" / "api" / "orders.py").write_text("def list_orders():\n    return []\n")
        index.refresh()
        assert "src/shop/api/orders.py" not in index.dependents_of("src/shop/models.py")

    def test_targeted_refresh_updates_one_file(self, pyrepo, index):
        target = pyrepo / "src" / "shop" / "models.py"
        target.write_text(target.read_text() + "\n\nclass Invoice:\n    pass\n")

        assert index.refresh_path("src/shop/models.py") is True
        assert index.has_symbol("Invoice")

    def test_targeted_refresh_handles_deletion(self, pyrepo, index):
        (pyrepo / "src" / "shop" / "api" / "orders.py").unlink()
        assert index.refresh_path("src/shop/api/orders.py") is True
        assert not index.file_exists("src/shop/api/orders.py")

    def test_targeted_refresh_is_a_no_op_when_unchanged(self, index):
        assert index.refresh_path("src/shop/models.py") is False

    def test_refresh_honours_a_minimum_interval(self, pyrepo, index):
        (pyrepo / "src" / "shop" / "new.py").write_text("x = 1\n")
        assert index.refresh(min_interval=60.0) == {"added": 0, "modified": 0, "removed": 0}
        assert index.refresh(min_interval=0.0)["added"] == 1


class TestAsyncBuild:
    def test_queries_are_safe_before_the_build_finishes(self, pyrepo):
        """An unbuilt index must answer "I don't know", never "does not exist"."""
        index = RepoIndex(pyrepo)
        assert index.is_built is False
        assert index.has_symbol("UserRepository") is False
        assert index.knows_type("UserRepository") is False  # crucially: also False
        assert index.file_exists("src/shop/models.py") is False
        assert index.blast_radius("anything") == set()
        assert index.tests_for("anything") == set()

    def test_build_async_completes(self, pyrepo):
        index = RepoIndex(pyrepo).build_async()
        assert index.ready(timeout=30) is True
        assert index.has_symbol("UserRepository")

    def test_ready_returns_false_while_building(self, pyrepo):
        index = RepoIndex(pyrepo)
        assert index.ready(timeout=0) is False

    def test_repeated_build_async_is_safe(self, pyrepo):
        index = RepoIndex(pyrepo)
        for _ in range(5):
            index.build_async()
        assert index.ready(timeout=30)


@pytest.mark.latency
class TestPerformance:
    def test_warm_lookups_are_effectively_free(self, index):
        """SPEC §8: repository lookups must not be felt."""
        iterations = 2000
        start = time.perf_counter()
        for _ in range(iterations):
            index.find_symbol("UserRepository")
            index.file_exists("src/shop/models.py")
            index.attributes_of("UserRepository")
        per_call_ms = ((time.perf_counter() - start) * 1000) / (iterations * 3)
        print(f"\n  warm lookup: {per_call_ms * 1000:.1f}µs per call")
        assert per_call_ms < 5.0

    def test_targeted_refresh_is_fast(self, pyrepo, index):
        target = pyrepo / "src" / "shop" / "models.py"
        samples = []
        for i in range(20):
            target.write_text(target.read_text() + f"\n# {i}\n")
            start = time.perf_counter()
            index.refresh_path("src/shop/models.py")
            samples.append((time.perf_counter() - start) * 1000)
        samples.sort()
        print(f"\n  refresh_path p50: {samples[len(samples) // 2]:.2f}ms")
        assert samples[len(samples) // 2] < 50.0
