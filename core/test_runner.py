"""Test runner: tests read the texts as written in code, in Russian (PQ-040).

The platform default language may be Ukrainian; tests of other languages
switch explicitly (``core.i18n.texts``, ``translation.override``).
"""

from django.test.runner import DiscoverRunner
from django.test.utils import override_settings


class BoteraTestRunner(DiscoverRunner):
    def setup_test_environment(self, **kwargs):
        self._russian = override_settings(LANGUAGE_CODE="ru")
        self._russian.enable()
        super().setup_test_environment(**kwargs)

    def teardown_test_environment(self, **kwargs):
        super().teardown_test_environment(**kwargs)
        self._russian.disable()
