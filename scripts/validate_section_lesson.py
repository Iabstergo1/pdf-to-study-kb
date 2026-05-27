"""小节学习讲义校验器

校验 Markdown 讲义的 frontmatter 和必备章节标题。

输入：Markdown 文件内容（字符串）
输出：{'passed': bool, 'errors': list[str]}

CLI 调用：
  python validate_section_lesson.py <file_path>

PASS 输出：
  {"passed": true, "errors": []}

FAIL 输出：
  {"passed": false, "errors": ["..."]}
"""
import sys
import re
import json
import yaml


# 必备章节标题
REQUIRED_HEADINGS = [
    "学习定位",
    "先记住的结论",
    "必须掌握",
    "首遍可略读",
    "核心概念",
    "模型结构、论证骨架或推导骨架",
    "直觉解释",
    "容易误解的点",
    "与个人知识体系的连接候选",
    "自测问题",
    "何时回原文",
    "原文定位",
]

# review_status 合法值
VALID_REVIEW_STATUS = {"draft", "reviewed", "accepted"}

# formula_risk 合法值
VALID_FORMULA_RISK = {"low", "medium", "high"}

# importance 合法值
VALID_IMPORTANCE = {"A", "B", "C"}

# generation_stage 合法值
VALID_GENERATION_STAGE = {"draft", "reviewed", "published"}


def parse_frontmatter(content: str) -> dict | None:
    """解析 YAML frontmatter，使用 yaml.safe_load"""
    match = re.match(r'^---\s*\n(.*?)\n---', content, re.DOTALL)
    if not match:
        return None

    try:
        return yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return None


def validate_section_lesson(content: str) -> dict:
    """校验小节讲义

    Args:
        content: Markdown 文件内容

    Returns:
        {'passed': bool, 'errors': list[str]}
    """
    errors = []

    # 1. 检查 frontmatter 存在
    if not content.strip().startswith('---'):
        return {'passed': False, 'errors': ['缺少 frontmatter（应以 --- 开头）']}

    fm = parse_frontmatter(content)
    if fm is None:
        return {'passed': False, 'errors': ['无法解析 frontmatter']}

    if not isinstance(fm, dict):
        return {'passed': False, 'errors': ['frontmatter 不是有效的 YAML mapping']}

    # 2. 检查必填字段
    required_fields = ['id', 'type', 'source_title', 'book_order', 'importance', 'difficulty', 'formula_risk', 'review_status', 'generation_stage']
    for field in required_fields:
        if field not in fm:
            errors.append(f"缺少必填字段: {field}")

    # 3. 检查 type 必须是 section-lesson
    if 'type' in fm and fm['type'] != 'section-lesson':
        errors.append(f"type 必须是 section-lesson，当前值: {fm['type']}")

    # 4. 检查 source_locator.pages
    if 'source_locator' not in fm:
        errors.append("缺少 source_locator")
    elif isinstance(fm.get('source_locator'), dict):
        if 'pages' not in fm['source_locator']:
            errors.append("source_locator 中缺少 pages")
        elif not isinstance(fm['source_locator']['pages'], list) or len(fm['source_locator']['pages']) == 0:
            errors.append("source_locator.pages 必须是非空列表")
    else:
        errors.append("source_locator 不是有效的 mapping")

    # 5. 检查 difficulty 必须是 1-5 的整数
    if 'difficulty' in fm:
        d = fm['difficulty']
        if not isinstance(d, int) or d < 1 or d > 5:
            errors.append(f"difficulty 必须是 1-5 的整数，当前值: {d}")

    # 6. 检查 review_status 合法性
    if 'review_status' in fm and fm['review_status'] not in VALID_REVIEW_STATUS:
        errors.append(f"非法 review_status: {fm['review_status']} (合法值: {VALID_REVIEW_STATUS})")

    # 7. 检查 formula_risk 合法性
    if 'formula_risk' in fm and fm['formula_risk'] not in VALID_FORMULA_RISK:
        errors.append(f"非法 formula_risk: {fm['formula_risk']} (合法值: {VALID_FORMULA_RISK})")

    # 8. 检查 importance 合法性
    if 'importance' in fm and fm['importance'] not in VALID_IMPORTANCE:
        errors.append(f"非法 importance: {fm['importance']} (合法值: {VALID_IMPORTANCE})")

    # 9. 检查 generation_stage 合法性
    if 'generation_stage' in fm and fm['generation_stage'] not in VALID_GENERATION_STAGE:
        errors.append(f"非法 generation_stage: {fm['generation_stage']} (合法值: {VALID_GENERATION_STAGE})")

    # 10. 检查必备章节标题
    headings = re.findall(r'^##\s+(.+)$', content, re.MULTILINE)
    for required in REQUIRED_HEADINGS:
        if required not in headings:
            errors.append(f"缺少必备标题: ## {required}")

    return {
        'passed': len(errors) == 0,
        'errors': errors
    }


def main():
    """CLI 入口"""
    if len(sys.argv) < 2:
        print("Usage: python validate_section_lesson.py <file_path>")
        sys.exit(1)

    file_path = sys.argv[1]

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except FileNotFoundError:
        print(json.dumps({'passed': False, 'errors': [f'文件不存在: {file_path}']}, ensure_ascii=False))
        sys.exit(1)

    result = validate_section_lesson(content)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result['passed'] else 1)


if __name__ == '__main__':
    main()
