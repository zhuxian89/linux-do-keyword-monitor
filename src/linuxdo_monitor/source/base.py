from abc import ABC, abstractmethod
from typing import List

from ..models import Post


class BaseSource(ABC):
    """Abstract base class for data sources"""

    @abstractmethod
    def fetch(self) -> List[Post]:
        """Fetch posts from the source"""
        pass

    @abstractmethod
    def get_source_name(self) -> str:
        """Return the name of this source for logging"""
        pass
