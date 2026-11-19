"""A script is named by its own file first, then by its package folder (Demoblaze trial:
`/node_modules/bootstrap-sweetalert/dist/sweetalert.min.js` was named Bootstrap)."""

from __future__ import annotations

import pytest

from labctl import pagerules


@pytest.mark.parametrize(("path", "name"), [
    ("/node_modules/bootstrap-sweetalert/dist/sweetalert.min.js", "SweetAlert"),
    ("/node_modules/bootstrap/dist/js/bootstrap.min.js", "Bootstrap"),
    ("/node_modules/video.js/dist/video.min.js", "video.js"),
    ("/node_modules/videojs-contrib-hls/dist/videojs-contrib-hls.min.js", "videojs-contrib-hls"),
    ("/js/angularjs/app.js", "AngularJS"),
    ("/scripts/jquery.validate.unobtrusive.min.js", "jQuery Validation Unobtrusive"),
    ("/js/index.js", None),
])  # fmt: skip
def test_library_name(path, name):
    assert pagerules.library_name(path, "") == name
