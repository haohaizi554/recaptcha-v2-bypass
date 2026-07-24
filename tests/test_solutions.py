"""
solutions.py 单元测试
=====================
测试方案注册表的完整性和纯逻辑函数:
  - SOLUTIONS 结构完整性 (必填字段/唯一 key)
  - SOLUTION_MAP 索引正确性
  - get_solution() 查找逻辑
  - check_dependency() 导入检测
  - check_solution_deps() 依赖检查

无外部依赖, 可在任何平台运行.
"""

import pytest

from solutions import (
    SOLUTION_COUNT,
    SOLUTION_MAP,
    SOLUTIONS,
    check_dependency,
    check_solution_deps,
    get_solution,
)


# ============================================================
# SOLUTIONS 结构完整性
# ============================================================
class TestSolutionsStructure:
    """验证 SOLUTIONS 列表中每个方案的字段完整性"""

    # 每个方案必须包含的字段
    REQUIRED_FIELDS = [
        "key",
        "name",
        "short_desc",
        "detail",
        "cost",
        "status",
        "status_color",
        "icon",
        "cli_icon",
        "deps",
    ]

    def test_solutions_not_empty(self):
        """SOLUTIONS 列表不应为空"""
        assert len(SOLUTIONS) > 0

    def test_solution_count_matches(self):
        """SOLUTION_COUNT 应等于 SOLUTIONS 长度"""
        assert len(SOLUTIONS) == SOLUTION_COUNT

    @pytest.mark.parametrize("solution", SOLUTIONS)
    def test_required_fields_present(self, solution):
        """每个方案必须包含所有必填字段"""
        for field in self.REQUIRED_FIELDS:
            assert field in solution, f"方案 '{solution.get('key', '?')}' 缺少字段: {field}"

    @pytest.mark.parametrize("solution", SOLUTIONS)
    def test_key_is_nonempty_string(self, solution):
        """key 必须是非空字符串"""
        assert isinstance(solution["key"], str)
        assert len(solution["key"]) > 0

    @pytest.mark.parametrize("solution", SOLUTIONS)
    def test_deps_is_list(self, solution):
        """deps 必须是列表"""
        assert isinstance(solution["deps"], list)

    @pytest.mark.parametrize("solution", SOLUTIONS)
    def test_status_color_is_hex(self, solution):
        """status_color 必须是合法的十六进制颜色码"""
        color = solution["status_color"]
        assert color.startswith("#"), f"方案 '{solution['key']}' 的 status_color 不以 # 开头"
        assert len(color) == 7, f"方案 '{solution['key']}' 的 status_color 长度异常: {color}"

    def test_keys_are_unique(self):
        """所有方案的 key 必须唯一"""
        keys = [s["key"] for s in SOLUTIONS]
        assert len(keys) == len(set(keys)), f"存在重复 key: {keys}"

    def test_known_keys_present(self):
        """验证核心方案 key 都存在"""
        expected_keys = {"audio", "api", "image", "cookie", "extension", "native"}
        actual_keys = {s["key"] for s in SOLUTIONS}
        missing = expected_keys - actual_keys
        assert not missing, f"缺少方案: {missing}"


# ============================================================
# SOLUTION_MAP 索引
# ============================================================
class TestSolutionMap:
    """验证 SOLUTION_MAP 的索引正确性"""

    def test_map_contains_all_solutions(self):
        """SOLUTION_MAP 应包含所有方案"""
        assert len(SOLUTION_MAP) == len(SOLUTIONS)

    def test_map_keys_match_solution_keys(self):
        """SOLUTION_MAP 的 key 应与 SOLUTIONS 中的 key 一致"""
        for sol in SOLUTIONS:
            assert sol["key"] in SOLUTION_MAP
            assert SOLUTION_MAP[sol["key"]] is sol


# ============================================================
# get_solution()
# ============================================================
class TestGetSolution:
    """测试 get_solution() 查找逻辑"""

    @pytest.mark.parametrize("solution", SOLUTIONS)
    def test_get_existing_solution(self, solution):
        """获取存在的方案应返回对应字典"""
        result = get_solution(solution["key"])
        assert result is not None
        assert result["key"] == solution["key"]

    def test_get_nonexistent_solution(self):
        """获取不存在的方案应返回 None"""
        assert get_solution("nonexistent_key_12345") is None

    def test_get_empty_key(self):
        """空 key 应返回 None"""
        assert get_solution("") is None


# ============================================================
# check_dependency()
# ============================================================
class TestCheckDependency:
    """测试 check_dependency() 导入检测"""

    def test_builtin_module_exists(self):
        """Python 内置模块应可导入"""
        assert check_dependency("os") is True
        assert check_dependency("sys") is True
        assert check_dependency("json") is True

    def test_nonexistent_module(self):
        """不存在的模块应返回 False"""
        assert check_dependency("definitely_not_a_real_module_xyz") is False


# ============================================================
# check_solution_deps()
# ============================================================
class TestCheckSolutionDeps:
    """测试 check_solution_deps() 依赖检查"""

    def test_nonexistent_solution(self):
        """不存在的方案应返回 (False, [])"""
        ok, missing = check_solution_deps("nonexistent_key_12345")
        assert ok is False
        assert missing == []

    @pytest.mark.parametrize("solution", SOLUTIONS)
    def test_returns_tuple(self, solution):
        """所有方案应返回 (bool, list) 元组"""
        ok, missing = check_solution_deps(solution["key"])
        assert isinstance(ok, bool)
        assert isinstance(missing, list)

    def test_missing_deps_subset_of_required(self):
        """缺失的依赖必须是方案声明依赖的子集"""
        for sol in SOLUTIONS:
            ok, missing = check_solution_deps(sol["key"])
            for dep in missing:
                assert dep in sol["deps"], f"方案 '{sol['key']}' 报告了未声明的依赖: {dep}"
