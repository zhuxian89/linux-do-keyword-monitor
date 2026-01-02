from abc import ABC, abstractmethod
from typing import List

from ..models import Post


class BaseMatcher(ABC):
    """Abstract base class for matchers"""

    @abstractmethod
    def match(self, post: Post, keyword: str) -> bool:
        """Check if post matches the keyword"""
        pass


class KeywordMatcher(BaseMatcher):
    """Simple case-insensitive keyword matcher"""

    def match(self, post: Post, keyword: str) -> bool:
        """Check if keyword exists in post title (case-insensitive)"""
        return keyword.lower() in post.title.lower()

    def find_matching_keywords(self, post: Post, keywords: List[str]) -> List[str]:
        """Find all keywords that match the post"""
        return [kw for kw in keywords if self.match(post, kw)]
