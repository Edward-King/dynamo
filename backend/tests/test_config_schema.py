"""
CacheConfig validation for the two explicit singular-static-URL default sizes
(default_static_thumbnail_size / default_static_icon_size).

Both must be members of cache.thumbnail_sizes so the singular static URL they
back always has a corresponding StaticFiles mount. The model_validator
accumulates ALL failures, so when both are invalid the error names both
fields, not just the first checked.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from photoshare.config.schema import CacheConfig


class TestDefaultStaticSizeValidation:
    def test_thumbnail_size_not_in_list_fails_naming_that_field(self):
        with pytest.raises(ValidationError) as excinfo:
            CacheConfig(
                thumbnail_sizes=[(320, 240), (800, 600)],
                default_static_thumbnail_size=(640, 480),
                default_static_icon_size=(320, 240),
            )
        msg = str(excinfo.value)
        assert "default_static_thumbnail_size" in msg
        # The valid values (the configured sizes) are named for the operator.
        assert "(320, 240)" in msg and "(800, 600)" in msg
        # The valid icon field is not falsely flagged.
        assert "default_static_icon_size" not in msg

    def test_icon_size_not_in_list_fails_naming_that_field(self):
        with pytest.raises(ValidationError) as excinfo:
            CacheConfig(
                thumbnail_sizes=[(320, 240), (800, 600)],
                default_static_thumbnail_size=(320, 240),
                default_static_icon_size=(1024, 768),
            )
        msg = str(excinfo.value)
        assert "default_static_icon_size" in msg
        assert "default_static_thumbnail_size" not in msg

    def test_both_invalid_error_mentions_both_field_names(self):
        with pytest.raises(ValidationError) as excinfo:
            CacheConfig(
                thumbnail_sizes=[(320, 240), (800, 600)],
                default_static_thumbnail_size=(640, 480),
                default_static_icon_size=(1024, 768),
            )
        msg = str(excinfo.value)
        # Both offending fields must be named -- not short-circuited.
        assert "default_static_thumbnail_size" in msg
        assert "default_static_icon_size" in msg


class TestDefaultStaticSizeDefaults:
    def test_defaults_are_320x240_and_backward_compatible_when_omitted(self):
        """Both keys default to (320,240) and are members of the default
        thumbnail_sizes, so omitting them entirely stays valid -- existing
        configs without the keys keep working."""
        cache = CacheConfig()
        assert cache.default_static_thumbnail_size == (320, 240)
        assert cache.default_static_icon_size == (320, 240)
        assert cache.default_static_thumbnail_size in cache.thumbnail_sizes
        assert cache.default_static_icon_size in cache.thumbnail_sizes
