"""The screen's picture shows no dialog; a dialog the recording showed open gets a picture of its
own; gradients and background images are design tokens."""

from __future__ import annotations

from labctl import har, lookfeel

PAGE = (
    "<html><head><title>Shop</title></head><body class='modal-open'>"
    "<nav class='navbar'>Home</nav><table id='cart'><tr><td>Xxxx</td></tr></table>"
    "<div class='modal fade show' id='videoModal' role='dialog' style='display: block;'>"
    "<h5 class='modal-title'>About us</h5></div>"
    "<div class='modal fade' id='exampleModal' role='dialog' aria-hidden='true'>"
    "<h5 class='modal-title'>New message</h5></div>"
    "<div class='modal-backdrop fade show'></div></body></html>"
)


def test_open_dialogs_of_a_snapshot():
    assert har.open_dialogs(PAGE) == ["#videoModal"]
    assert har.open_dialogs(PAGE.replace("fade show' id='videoModal'", "fade' id='videoModal'")
                            .replace(" style='display: block;'", "")) == []  # fmt: skip


def test_the_screen_is_drawn_without_and_the_dialog_with():
    plain = lookfeel.without_dialogs(PAGE)
    assert '[id="videoModal"]{display:none!important}' in plain and ".modal-backdrop" in plain
    page = PAGE.replace("'", '"')
    shown = lookfeel.with_dialog_open(page, "#exampleModal")
    assert 'id="exampleModal"' in shown and 'class="modal fade show"' in shown
    assert 'aria-hidden="true"' not in shown.split('id="exampleModal"')[0][-120:] + shown.split(
        'id="exampleModal"')[1][:80]  # fmt: skip
    assert '[id="videoModal"]{display:none!important}' in shown  # the other one closed
    assert lookfeel.with_dialog_open(page, "#nothere") is None


def test_gradients_are_tokens():
    value = "linear-gradient(135deg, rgb(76, 0, 255), rgb(0, 207, 255))"
    assert lookfeel.background_image(value) == "linear-gradient(135deg, #4c00ff, #00cfff)"
    url = f'url("{lookfeel.RENDER_HOST}/assets/logo.png")'
    assert lookfeel.background_image(url) == "url(/assets/logo.png)"
    look = {"roles": {"header": [{"count": 1, "sample": "nav.navbar", "classes": ["navbar"],
                                  "style": {"background": "rgb(41, 43, 44)",
                                            "background_image": value}}],
                      "footer": [{"count": 1, "sample": "footer", "classes": [],
                                  "style": {"background": "rgb(41, 43, 44)",
                                            "background_image": value}}]}}  # fmt: skip
    colors = lookfeel.theme({"home": look})["colors"]
    assert colors["header_background"] == "#292b2c"
    assert colors["header_background_image"] == "linear-gradient(135deg, #4c00ff, #00cfff)"
    assert colors["footer_background_image"] == "linear-gradient(135deg, #4c00ff, #00cfff)"
