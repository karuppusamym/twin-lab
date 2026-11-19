"""Interface text and static files of the application stay readable; data next to them stays
masked (static file names, media player controls, words of prose, values shown whole)."""

from __future__ import annotations

from labctl import har, sanitize


def test_static_file_names_of_the_own_origin_are_kept():
    html = ('<html><body><img src="blazemeter-favicon-512x512.png"><img src="imgs/Lumia_1520.jpg">'
            '<img src="/img/10023.png"><img src="/avatars/Jane_Smith_1980.png">'
            '<img src="https://cdn.example.test/a/Lumia_1520.jpg"><img src="user_4711.jpg">'
            '<a href="/orders/104522/invoice.css">x</a><td>Order 104522</td></body></html>')  # fmt: skip
    out = sanitize.page(html).html
    assert 'src="blazemeter-favicon-512x512.png"' in out
    assert 'src="imgs/Lumia_1520.jpg"' in out
    # a file named by a number, under a user folder, of another host or named by its key: masked
    assert "10023" not in out and "1980" not in out and "4711" not in out
    assert "/a/Lumia_9999.jpg" in out
    # a record number in a folder is masked, the file name kept
    assert "/orders/{id}/invoice.css" in out or "/orders/999999/invoice.css" in out
    assert "104522" not in out


def test_media_player_controls_are_the_librarys_text():
    html = ('<html><body><div class="video-js vjs-default-skin" id="example-video">'
            '<button class="vjs-play-control"><span class="vjs-control-text">Play Video</span>'
            '</button><div class="vjs-current-time"><span class="vjs-control-text">Current Time'
            '</span> 1:23</div><div class="vjs-modal-dialog" aria-label="Modal Window">'
            '<p class="vjs-modal-dialog-description vjs-control-text" '
            'id="example-video_component_312_description">This is a modal window.</p></div></div>'
            '<h3 id="customer">Jane Smith</h3><p id="owner-note">Called Jane Smith</p>'
            '</body></html>')  # fmt: skip
    out = sanitize.page(html, known={"jane", "smith"}).html
    assert ">Play Video<" in out and ">Current Time<" in out
    assert ">This is a modal window.<" in out and 'aria-label="Modal Window"' in out
    assert "1:23" not in out  # a time stays masked inside the player too
    assert "Jane" not in out and "Smith" not in out


def test_player_class_does_not_open_other_elements():
    html = '<html><body><div class="vjs-poster" id="who">Jane Smith</div></body></html>'
    out = sanitize.page(html, known={"jane", "smith"}).html
    assert "Jane" not in out  # a name inside the player is still a name (it is known as data)


def test_prose_capitals_are_not_names():
    answer = {
        "Items": [
            {
                "desc": "A 13-inch and 15-inch model with the Touch Bar, a thin, light "
                "design and a bright display for every day.",
                "owner": "Ann Hartley",
            }
        ]
    }
    assert "touch" not in sanitize.names_in(answer, prose=False)
    assert {"ann", "hartley"} <= sanitize.names_in(answer, prose=False)
    known = sanitize.names_in(answer, prose=False)
    out = sanitize.page("<html><body><h4><b>Get in Touch</b></h4><h4>Hartley</h4>"
                        "<h2>Ann Hartley</h2></body></html>", known).html  # fmt: skip
    assert "Get in Touch" in out
    assert "Hartley" not in out and "Ann" not in out


def test_a_value_shown_whole_is_masked_whole():
    answer = {"Items": [{"title": "Samsung galaxy s6"}, {"title": "HTC One M9"},
                        {"title": "Nexus 6"}, {"cat": "phone"}]}  # fmt: skip
    known = sanitize.names_in(answer, prose=False) | sanitize.phrases_in(answer)
    html = ('<html><body><nav><a href="index.html">Home</a></nav><div id="tbodyid">'
            '<h4 class="card-title"><a href="prod.html?idp_=1" class="hrefch">Samsung galaxy s6'
            '</a></h4><h4 class="card-title"><a class="hrefch" href="#">HTC One M9</a></h4>'
            '<h4 class="card-title"><a class="hrefch" href="#">Nexus 6</a></h4></div>'
            '<a class="list-group-item">Phones</a><button>Add to cart</button></body></html>')  # fmt: skip
    out = sanitize.page(html, known).html
    assert "galaxy" not in out and "One" not in out and "Nexus" not in out
    assert "Xxxxxxx xxxxxx x9" in out and "XXX Xxx X9" in out
    assert ">Home<" in out and ">Phones<" in out and ">Add to cart<" in out


def test_page_knowledge_reads_phrases_from_answers():
    class E:
        def __init__(self, data):
            self.resp_json, self.status, self.resp_text, self.resp_mime = data, 200, None, ""

    known, _ = har.page_knowledge([], [E({"Items": [{"title": "Sony vaio i5"}]})])
    assert "sony vaio i5" in known and "sony" in known
