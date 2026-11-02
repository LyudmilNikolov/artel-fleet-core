"""The user guide describes the current system, in both languages, with no missing translations."""

import re
from html.parser import HTMLParser

import pytest


def visible_text(html: str) -> str:
    class Collector(HTMLParser):
        def __init__(self):
            super().__init__()
            self.skip, self.parts = 0, []

        def handle_starttag(self, tag, attrs):
            self.skip += tag in ("script", "style")

        def handle_endtag(self, tag):
            self.skip -= tag in ("script", "style")

        def handle_data(self, data):
            if not self.skip and data.strip():
                self.parts.append(data.strip())

    collector = Collector()
    collector.feed(html)
    return " ".join(collector.parts)


EXPECTED = {
    "en": [
        "Giving a driver a login",
        "Assigning a vehicle",
        "What a driver sees",
        "Approval of driver changes",
        "Planned services and due dates",
        "Status colours",
        "Only trucks and cars can be assigned",
        "audit log",
    ],
    "bg": [
        "Акаунт за вход на водач",
        "Назначаване на превозно средство",
        "Какво вижда водачът",
        "Одобрение на промените на водачите",
        "Планови обслужвания и срокове",
        "Цветове на състоянията",
    ],
}


@pytest.mark.asyncio
@pytest.mark.parametrize("lang", ["en", "bg"])
async def test_guide_covers_the_current_features_without_raw_keys(client, db_session, admin_user, lang):
    await db_session.commit()
    assert (await client.post("/login", data={"username": "testadmin", "password": "testpass123"})).status_code == 302
    await client.get(f"/set-language/{lang}")
    page = await client.get("/help")
    assert page.status_code == 200
    text = visible_text(page.text)
    assert not re.findall(r"\bhelp\.[a-z_]+\b", text), "untranslated guide keys are visible"
    for phrase in EXPECTED[lang]:
        assert phrase in text, phrase
    # statements that used to be wrong must be gone
    assert "Most list pages and all reports support export" not in text
    assert "or more vehicles" not in text and "няколко превозни средства" not in text
    if lang == "bg":
        assert "шофьор" not in text.lower()  # the product says "водач" everywhere


@pytest.mark.asyncio
async def test_guide_is_open_to_every_role_including_a_driver_without_a_vehicle(client, db_session, viewer_user):
    await db_session.commit()
    assert (await client.post("/login", data={"username": "testviewer", "password": "testpass123"})).status_code == 302
    assert (await client.get("/help")).status_code == 200


# --- the driver guide ----------------------------------------------------------------------------------------

from tests.test_web.test_driver_access import login as driver_login  # noqa: E402
from tests.test_web.test_driver_access import world  # noqa: E402, F401

FULL_GUIDE_ONLY = [
    "Giving a driver a login",
    "System Overview",
    "Audit Log",
    "Users → Add user",
    "Planned services",
    "Settings",
]


async def guide_text(client, lang):
    await client.get(f"/set-language/{lang}")
    page = await client.get("/help")
    assert page.status_code == 200
    text = visible_text(page.text)
    assert not re.findall(r"\bhelp\.[a-z_]+\b", text), "untranslated guide keys are visible"
    return text


@pytest.mark.asyncio
@pytest.mark.parametrize("lang", ["en", "bg"])
async def test_driver_without_a_vehicle_gets_only_the_driver_guide(client, world, lang):  # noqa: F811
    await driver_login(client, "driver_none")
    text = await guide_text(client, lang)
    if lang == "en":
        for phrase in (
            "Driver Guide",
            "Licences & documents",
            "Submit for approval",
            "Uploading files",
            "No vehicle is assigned to you yet",
        ):
            assert phrase in text, phrase
        for phrase in FULL_GUIDE_ONLY:
            assert phrase not in text, phrase
        assert "Add expense" not in text  # the vehicle steps appear only once a vehicle is assigned
    else:
        for phrase in ("Ръководство за водача", "Лицензи и документи", "Все още нямате назначено превозно средство"):
            assert phrase in text, phrase
        assert "Одобрение на промените на водачите" not in text and "шофьор" not in text.lower().replace(
            "шофьорска", ""
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("lang", ["en", "bg"])
async def test_driver_with_a_vehicle_also_gets_the_vehicle_steps(client, world, lang):  # noqa: F811
    await driver_login(client, "driver_a")
    text = await guide_text(client, lang)
    if lang == "en":
        assert "Driver Guide" in text and "Add expense" in text and "Add maintenance" in text
        assert "No vehicle is assigned to you yet" not in text
        for phrase in FULL_GUIDE_ONLY:
            assert phrase not in text, phrase
    else:
        assert "Ръководство за водача" in text and "Все още нямате назначено" not in text


@pytest.mark.asyncio
async def test_managers_and_viewers_still_get_the_full_guide(client, db_session, fleet_manager_user, viewer_user):
    await db_session.commit()
    for username in ("testmanager", "testviewer"):
        await client.post("/logout")
        assert (await client.post("/login", data={"username": username, "password": "testpass123"})).status_code == 302
        await client.get("/set-language/en")
        text = visible_text((await client.get("/help")).text)
        assert "FleetCore User Guide" in text and "Giving a driver a login" in text and "Driver Guide" not in text
