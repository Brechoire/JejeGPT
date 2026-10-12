"""Isolation des tests : vide les caches Django avant chaque test.

Les caches LocMem survivent au rollback transactionnel de pytest-django
(TestCase) : sans ceci, AppSettings.get_cached() ou le HTML Markdown mémorisé
fuiraient d'un test à l'autre (FK pendantes, flags périmés).
"""
import pytest
from django.core.cache import cache


@pytest.fixture(autouse=True)
def _clear_caches():
    cache.clear()
    yield
    cache.clear()
