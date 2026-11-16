"""Built-in synthetic dictionaries for `dict_hmac`. Versioned: changing a list changes every
masked value, so a change is a new name (`_v2`), never an edit of `_v1`."""

from __future__ import annotations

_FIRST = """
Aaron Abigail Ada Adrian Aiden Alba Alden Alma Alvin Amara Ambrose Amelia Anders Anika Ansel
Arden Ariel Arlo Astrid Atlas August Aurora Basil Beatrix Bennett Bianca Bodhi Bram Brielle
Bryn Cade Calla Calvin Camila Carys Cedric Celeste Clara Cleo Colette Conrad Cora Corbin Dahlia
Dalia Damon Daphne Darius Delia Desmond Dorian Eamon Edie Edmund Eliza Elliot Elodie Emil Enzo
Esme Etta Evander Fabian Faye Felix Fern Fiona Flora Florian Gemma Gideon Greta Griffin Hale
Hazel Henrik Hollis Ida Ilse Imogen Ines Iris Ivo Jasper Juno Kai Keira Lark Leif Lena Linus
Lior Lotte Luca Mabel Magnus Maren Marlo Matilda Milo Mira Nadia Nell Niko Nora Odette Olin
Orla Otto Pax Petra Pia Quentin Quinn Rafe Remy Rhea Rocco Rosalind Rune Sage Saoirse Silas
Soren Stellan Tamsin Tessa Theo Thea Tobias Una Ursa Valen Vera Wren Xavi Yara Yves Zara Zeke
"""
_LAST = """
Abbott Ainsley Alder Ashby Ashcombe Bancroft Barlow Beacham Bellamy Blackwood Bramley Brightwater
Calloway Carrow Castell Chandry Clayborne Colter Copeland Corwin Crane Dalloway Darrow Dunmore
Easton Ellery Elmsworth Emberly Fairley Farrow Fenwick Fielding Fletcher Foxley Galloway Garrick
Gilchrist Greaves Hadley Halloran Harrow Hawthorne Hayward Hollins Huxley Ingram Iverson Jessop
Keaton Kendrick Kingsley Kirkwood Lachlan Langley Larkin Lockridge Lowell Maddox Marchetti
Merrick Montrose Morrow Nash Nettleton Northcott Oakes Oldham Orwell Pemberton Penrose Pickering
Prescott Quarles Radley Ramsey Ravenscroft Redfern Renwick Ridley Rockwell Rowntree Sallow
Sandford Selwyn Shelby Sinclair Sterling Stroud Sutcliffe Talbot Tamblyn Thackeray Thornbury
Tilbury Trevane Underhill Upton Vance Varley Wakefield Waverley Westbrook Whitlock Winslow Wolcott
Wren Yardley Yelland Yorke Zeller Brackley Coldwell Dunstan Everard Fairbank Hartwell Kestrel
Linfield Mayhew Norwood Pendry Quinlan Rushworth Stanwick Tremayne Vickery Wexford Ashdown
"""
_STREET_NAMES = """
Alder Aspen Birch Bluebell Bramble Cedar Chestnut Clover Copper Cypress Elder Fern Foxglove
Hawthorn Hazel Heather Holly Juniper Larch Laurel Linden Magnolia Maple Meadow Myrtle Oak Orchard
Pine Poplar Quarry Rowan Sage Sorrel Spruce Sycamore Thistle Willow Yarrow Lantern Harbor
"""
_STREET_KINDS = "Street Lane Road Avenue Close Way Drive Court Place Row Terrace Crescent"
_TOWN_HEADS = """
Ash Bel Brook Clear Cold Crane Deer East Elm Fair Fox Glen Green Hart High Kings Lake Lin Marsh
Mill North Oak Pine Red Ridge Rose Silver Stone Sun West White Wild Wood
"""
_TOWN_TAILS = "field ford dale ton bury wick haven stead port mouth vale wood"

FIRST_NAMES_V1 = _FIRST.split()
LAST_NAMES_V1 = _LAST.split()
STREETS_V1 = [f"{n} {k}" for n in _STREET_NAMES.split() for k in _STREET_KINDS.split()]
CITIES_V1 = [f"{h}{t}" for h in _TOWN_HEADS.split() for t in _TOWN_TAILS.split()]

DICTIONARIES: dict[str, list[str]] = {
    "first_names_v1": FIRST_NAMES_V1,
    "last_names_v1": LAST_NAMES_V1,
    "streets_v1": STREETS_V1,  # a house number from the same HMAC is put in front
    "cities_v1": CITIES_V1,
}
WITH_NUMBER = {"streets_v1"}
FULL_NAMES = "full_names_v1"  # first + last, each from its own HMAC
