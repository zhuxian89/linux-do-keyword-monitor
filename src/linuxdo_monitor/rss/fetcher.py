from abc import ABC, abstractmethod
from typing import List

from ..models import Post


class BaseFetcher(ABC):
    """Abstract base class for RSS fetchers"""

    @abstractmethod
    def fetch(self) -> str:
        """Fetch RSS content and return raw XML string"""
        pass


class HttpFetcher(BaseFetcher):
    """HTTP-based RSS fetcher"""

    def __init__(self, url: str, timeout: int = 30):
        self.url = url
        self.timeout = timeout

    def fetch(self) -> str:
        """Fetch RSS content via HTTP"""
        import urllib.request

        req = urllib.request.Request(
            self.url,
            headers={"User-Agent": "LinuxDoMonitor/1.0"}
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as response:
            return response.read().decode("utf-8")
