"""
Cache module with pluggable backends.
Supports in-memory cache by default, can be switched to Redis.
"""
import logging
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


class CacheBackend(ABC):
    """Abstract cache backend interface"""

    @abstractmethod
    def get(self, key: str) -> Optional[Any]:
        """Get value by key"""
        pass

    @abstractmethod
    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        """Set value with optional TTL in seconds"""
        pass

    @abstractmethod
    def delete(self, key: str) -> None:
        """Delete key"""
        pass

    @abstractmethod
    def exists(self, key: str) -> bool:
        """Check if key exists"""
        pass

    @abstractmethod
    def sadd(self, key: str, *values: Any) -> None:
        """Add values to a set"""
        pass

    @abstractmethod
    def sismember(self, key: str, value: Any) -> bool:
        """Check if value is in set"""
        pass

    @abstractmethod
    def smembers(self, key: str) -> Set[Any]:
        """Get all members of a set"""
        pass

    @abstractmethod
    def clear(self) -> None:
        """Clear all cache"""
        pass


class MemoryCache(CacheBackend):
    """In-memory cache implementation"""

    def __init__(self):
        self._cache: Dict[str, Any] = {}
        self._sets: Dict[str, Set[Any]] = {}
        self._expiry: Dict[str, float] = {}

    def _is_expired(self, key: str) -> bool:
        """Check if key is expired"""
        if key in self._expiry:
            if time.time() > self._expiry[key]:
                self.delete(key)
                return True
        return False

    def get(self, key: str) -> Optional[Any]:
        if self._is_expired(key):
            return None
        return self._cache.get(key)

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        self._cache[key] = value
        if ttl:
            self._expiry[key] = time.time() + ttl

    def delete(self, key: str) -> None:
        self._cache.pop(key, None)
        self._sets.pop(key, None)
        self._expiry.pop(key, None)

    def exists(self, key: str) -> bool:
        if self._is_expired(key):
            return False
        return key in self._cache or key in self._sets

    def sadd(self, key: str, *values: Any) -> None:
        if key not in self._sets:
            self._sets[key] = set()
        self._sets[key].update(values)

    def sismember(self, key: str, value: Any) -> bool:
        if key not in self._sets:
            return False
        return value in self._sets[key]

    def smembers(self, key: str) -> Set[Any]:
        return self._sets.get(key, set()).copy()

    def clear(self) -> None:
        self._cache.clear()
        self._sets.clear()
        self._expiry.clear()


class RedisCache(CacheBackend):
    """Redis cache implementation (placeholder for future use)"""

    def __init__(self, host: str = "localhost", port: int = 6379, db: int = 0):
        try:
            import redis
            self._client = redis.Redis(host=host, port=port, db=db, decode_responses=True)
            self._client.ping()
            logger.info(f"Connected to Redis at {host}:{port}")
        except ImportError:
            raise ImportError("redis package not installed. Run: pip install redis")
        except Exception as e:
            raise ConnectionError(f"Failed to connect to Redis: {e}")

    def get(self, key: str) -> Optional[Any]:
        import json
        value = self._client.get(key)
        if value:
            try:
                return json.loads(value)
            except (json.JSONDecodeError, TypeError, ValueError):
                return value
        return None

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        import json
        if isinstance(value, (dict, list)):
            value = json.dumps(value)
        if ttl:
            self._client.setex(key, ttl, value)
        else:
            self._client.set(key, value)

    def delete(self, key: str) -> None:
        self._client.delete(key)

    def exists(self, key: str) -> bool:
        return bool(self._client.exists(key))

    def sadd(self, key: str, *values: Any) -> None:
        if values:
            self._client.sadd(key, *[str(v) for v in values])

    def sismember(self, key: str, value: Any) -> bool:
        return bool(self._client.sismember(key, str(value)))

    def smembers(self, key: str) -> Set[Any]:
        return self._client.smembers(key)

    def clear(self) -> None:
        self._client.flushdb()


class AppCache:
    """Application-level cache with domain-specific methods and forum isolation"""

    # Cache key prefixes
    PREFIX_KEYWORDS = "keywords"
    PREFIX_SUBSCRIBERS = "subscribers:"
    PREFIX_SUBSCRIBE_ALL = "subscribe_all"
    PREFIX_NOTIFIED = "notified:"
    PREFIX_AUTHORS = "authors"
    PREFIX_AUTHOR_SUBSCRIBERS = "author_subscribers:"

    def __init__(self, forum_id: str = "default", backend: Optional[CacheBackend] = None):
        self.forum_id = forum_id
        self._backend = backend or MemoryCache()

    def _key(self, key: str) -> str:
        """Generate forum-scoped cache key"""
        return f"{self.forum_id}:{key}"

    @property
    def backend(self) -> CacheBackend:
        return self._backend

    def switch_backend(self, backend: CacheBackend) -> None:
        """Switch to a different cache backend"""
        self._backend = backend

    # Keywords cache
    def get_keywords(self) -> Optional[List[str]]:
        """Get cached keywords list"""
        return self._backend.get(self._key(self.PREFIX_KEYWORDS))

    def set_keywords(self, keywords: List[str], ttl: int = 3600) -> None:
        """Cache keywords list with TTL (default 1 hour)"""
        self._backend.set(self._key(self.PREFIX_KEYWORDS), keywords, ttl)

    def invalidate_keywords(self) -> None:
        """Invalidate keywords cache"""
        self._backend.delete(self._key(self.PREFIX_KEYWORDS))

    # Subscribers cache
    def get_subscribers(self, keyword: str) -> Optional[List[int]]:
        """Get cached subscribers for a keyword"""
        return self._backend.get(self._key(f"{self.PREFIX_SUBSCRIBERS}{keyword}"))

    def set_subscribers(self, keyword: str, chat_ids: List[int], ttl: int = 3600) -> None:
        """Cache subscribers for a keyword (default 1 hour)"""
        self._backend.set(self._key(f"{self.PREFIX_SUBSCRIBERS}{keyword}"), chat_ids, ttl)

    def invalidate_subscribers(self, keyword: Optional[str] = None) -> None:
        """Invalidate subscribers cache for a keyword or all"""
        if keyword:
            self._backend.delete(self._key(f"{self.PREFIX_SUBSCRIBERS}{keyword}"))
        # Note: For full invalidation, would need key pattern matching

    # Subscribe all users cache
    def get_subscribe_all_users(self) -> Optional[List[int]]:
        """Get cached subscribe_all users"""
        return self._backend.get(self._key(self.PREFIX_SUBSCRIBE_ALL))

    def set_subscribe_all_users(self, chat_ids: List[int], ttl: int = 3600) -> None:
        """Cache subscribe_all users (default 1 hour)"""
        self._backend.set(self._key(self.PREFIX_SUBSCRIBE_ALL), chat_ids, ttl)

    def invalidate_subscribe_all(self) -> None:
        """Invalidate subscribe_all cache"""
        self._backend.delete(self._key(self.PREFIX_SUBSCRIBE_ALL))

    # Notification tracking (for current fetch cycle)
    def mark_notified(self, chat_id: int, post_id: str) -> None:
        """Mark that a user has been notified about a post"""
        self._backend.sadd(self._key(f"{self.PREFIX_NOTIFIED}{post_id}"), chat_id)

    def is_notified(self, chat_id: int, post_id: str) -> bool:
        """Check if user was already notified about a post in this cycle"""
        return self._backend.sismember(self._key(f"{self.PREFIX_NOTIFIED}{post_id}"), chat_id)

    def clear_notified(self, post_id: str) -> None:
        """Clear notification tracking for a post"""
        self._backend.delete(self._key(f"{self.PREFIX_NOTIFIED}{post_id}"))

    # Author subscription cache
    def get_authors(self) -> Optional[List[str]]:
        """Get cached subscribed authors list"""
        return self._backend.get(self._key(self.PREFIX_AUTHORS))

    def set_authors(self, authors: List[str], ttl: int = 3600) -> None:
        """Cache subscribed authors list with TTL (default 1 hour)"""
        self._backend.set(self._key(self.PREFIX_AUTHORS), authors, ttl)

    def invalidate_authors(self) -> None:
        """Invalidate authors cache"""
        self._backend.delete(self._key(self.PREFIX_AUTHORS))

    def get_author_subscribers(self, author: str) -> Optional[List[int]]:
        """Get cached subscribers for an author"""
        return self._backend.get(self._key(f"{self.PREFIX_AUTHOR_SUBSCRIBERS}{author}"))

    def set_author_subscribers(self, author: str, chat_ids: List[int], ttl: int = 3600) -> None:
        """Cache subscribers for an author (default 1 hour)"""
        self._backend.set(self._key(f"{self.PREFIX_AUTHOR_SUBSCRIBERS}{author}"), chat_ids, ttl)

    def invalidate_author_subscribers(self, author: Optional[str] = None) -> None:
        """Invalidate author subscribers cache"""
        if author:
            self._backend.delete(self._key(f"{self.PREFIX_AUTHOR_SUBSCRIBERS}{author}"))

    def clear_all(self) -> None:
        """Clear all cache for this forum"""
        # Note: This clears ALL cache, not just this forum's
        # For forum-specific clearing, would need key pattern matching
        self._backend.clear()


# Global cache instance
_cache: Optional[AppCache] = None


def get_cache() -> AppCache:
    """Get or create global cache instance"""
    global _cache
    if _cache is None:
        _cache = AppCache()
    return _cache


def init_cache(backend: Optional[CacheBackend] = None) -> AppCache:
    """Initialize cache with specific backend"""
    global _cache
    _cache = AppCache(backend)
    return _cache
