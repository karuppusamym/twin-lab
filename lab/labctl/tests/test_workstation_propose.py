"""Targets proposed from a UiPath project's XAML (labctl.workstation.propose)."""

from __future__ import annotations

from pathlib import Path

import pytest

from labctl.workstation.levels import Targets
from labctl.workstation.propose import propose

MAIN = """<Activity xmlns="http://schemas.microsoft.com/netfx/2009/xaml/activities"
  xmlns:ui="http://schemas.uipath.com/workflow/activities" xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml">
  <x:Members><x:Property Name="in_Url" Type="InArgument(x:String)" /></x:Members>
  <this:Main.in_Url><InArgument x:TypeArguments="x:String">https://orders.example.test/login</InArgument></this:Main.in_Url>
  <ui:OpenBrowser BrowserType="Edge" Url="[in_Url]" />
  <ui:TypeInto SimulateType="True" Text="[account]">
    <ui:TypeInto.Target><ui:Target Selector="&lt;html app='msedge.exe' url='https://orders.example.test/*' /&gt;&lt;webctrl id='q' /&gt;" /></ui:TypeInto.Target>
  </ui:TypeInto>
  <ui:Click SimulateClick="True">
    <ui:Click.Target><ui:Target Selector="&lt;wnd app='casedesk.exe' cls='WindowsForms10*' /&gt;&lt;ctrl automationid='btnSave' /&gt;" /></ui:Click.Target>
  </ui:Click>
  <ui:OpenApplication FileName="C:\\Apps\\CaseDesk\\CaseDesk.exe" />
  <ui:Click><ui:Click.Target><ui:Target Selector="&lt;wnd app='javaw.exe' /&gt;" /></ui:Click.Target></ui:Click>
</Activity>"""
TERMINAL = """<Activity xmlns:ut="http://schemas.uipath.com/workflow/activities/terminal">
  <ut:TerminalSession Host="mainframe.example.test" Port="992" Protocol="TN3270" />
  <ut:TerminalSession Host="[cfg_host]" />
</Activity>"""


def test_targets_browser_and_notes(tmp_path: Path):
    (tmp_path / "Main.xaml").write_text(MAIN, encoding="utf-8")
    (tmp_path / "Terminal.xaml").write_text(TERMINAL, encoding="utf-8")
    (tmp_path / ".local").mkdir()
    (tmp_path / ".local" / "Cache.xaml").write_text("<wnd app='ignored.exe' />", encoding="utf-8")
    got = propose(tmp_path)
    keys = [t.get("exe") or t.get("origin") or t.get("tn3270") for t in got["targets"]]
    assert keys == [
        "casedesk.exe",
        "javaw.exe",
        "https://orders.example.test",
        "mainframe.example.test:992",
    ]
    assert got["browser"] == "edge"
    notes = " ".join(got["notes"])
    assert "javaw.exe is recorded only as used" in notes
    assert "SimulateClick" in notes and "SimulateType" in notes
    assert "Terminal.xaml: an address or host comes from a variable" in notes
    assert "schemas" not in " ".join(keys)
    # the proposal is what a session reads
    Targets.from_list([{k: v for k, v in t.items() if k != "uses"} for t in got["targets"]])


def test_a_folder_without_xaml(tmp_path):
    with pytest.raises(FileNotFoundError):
        propose(tmp_path)
