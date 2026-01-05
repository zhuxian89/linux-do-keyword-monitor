import re
import logging
from abc import ABC, abstractmethod
from typing import List, Optional, Tuple

from ..models import Post

logger = logging.getLogger(__name__)

# 正则表达式安全限制
MAX_REGEX_LENGTH = 200  # 最大正则长度
REGEX_TIMEOUT_PATTERN = re.compile(r'(.+)\1{10,}')  # 检测可能导致回溯的模式


class BaseMatcher(ABC):
    """Abstract base class for matchers"""

    @abstractmethod
    def match(self, post: Post, keyword: str) -> bool:
        """Check if post matches the keyword"""
        pass


def is_regex_pattern(keyword: str) -> bool:
    """检测关键词是否是正则表达式

    判断依据：包含正则特殊字符（排除常见的普通字符组合）
    """
    # 正则特殊字符
    regex_chars = r'[\^\$\.\*\+\?\{\}\[\]\(\)\|\\]'
    return bool(re.search(regex_chars, keyword))


def validate_regex(pattern: str) -> Tuple[bool, Optional[str]]:
    """验证正则表达式是否安全有效

    Returns:
        (is_valid, error_message)
    """
    # 长度检查
    if len(pattern) > MAX_REGEX_LENGTH:
        return False, f"正则表达式过长（最大 {MAX_REGEX_LENGTH} 字符）"

    # 危险模式检查（可能导致 ReDoS）
    dangerous_patterns = [
        r'\(\.\*\)\+',      # (.*)+
        r'\(\.\+\)\+',      # (.+)+
        r'\(\.\*\)\*',      # (.*)*
        r'\(\.\+\)\*',      # (.+)*
        r'\([^\)]+\)\{[0-9]+,\}',  # 大量重复
    ]
    for dp in dangerous_patterns:
        if re.search(dp, pattern):
            return False, "正则表达式包含可能导致性能问题的模式"

    # 尝试编译
    try:
        re.compile(pattern, re.IGNORECASE)
        return True, None
    except re.error as e:
        return False, f"正则语法错误: {e}"


class KeywordMatcher(BaseMatcher):
    """支持普通关键词和正则表达式的匹配器

    - 普通关键词：使用包含匹配（不区分大小写）
    - 正则表达式：使用 re.search 匹配（不区分大小写）
    """

    # 缓存已编译的正则表达式
    _regex_cache: dict = {}

    def _get_compiled_regex(self, pattern: str) -> Optional[re.Pattern]:
        """获取编译后的正则表达式（带缓存）"""
        if pattern not in self._regex_cache:
            try:
                self._regex_cache[pattern] = re.compile(pattern, re.IGNORECASE)
            except re.error:
                self._regex_cache[pattern] = None
        return self._regex_cache[pattern]

    def match(self, post: Post, keyword: str) -> bool:
        """检查帖子是否匹配关键词

        - 如果关键词包含正则特殊字符，尝试作为正则匹配
        - 否则使用普通的包含匹配
        """
        title = post.title

        if is_regex_pattern(keyword):
            # 正则匹配
            compiled = self._get_compiled_regex(keyword)
            if compiled:
                try:
                    return bool(compiled.search(title))
                except Exception as e:
                    logger.warning(f"正则匹配异常 '{keyword}': {e}")
                    # 回退到普通匹配
                    return keyword.lower() in title.lower()
            else:
                # 正则无效，回退到普通匹配
                return keyword.lower() in title.lower()
        else:
            # 普通包含匹配
            return keyword.lower() in title.lower()

    def find_matching_keywords(self, post: Post, keywords: List[str]) -> List[str]:
        """Find all keywords that match the post"""
        return [kw for kw in keywords if self.match(post, kw)]
