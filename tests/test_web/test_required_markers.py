"""QA #32: every field a form requires has the same red asterisk, and the form says what it means."""

from html.parser import HTMLParser

import pytest

from tests.test_web.test_expenses import _login

FORMS = ["/vehicles/new", "/expenses/new", "/maintenance/new", "/drivers/new", "/compliance/new"]


class Fields(HTMLParser):
    def __init__(self):
        super().__init__()
        self.required_ids: list[str] = []
        self.labels: dict[str, str] = {}

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "label" and values.get("for"):
            self.labels[values["for"]] = values.get("class") or ""
        if tag in ("input", "select", "textarea") and "required" in values and values.get("type") != "hidden":
            self.required_ids.append(values.get("id") or values.get("name") or "?")


@pytest.mark.asyncio
@pytest.mark.parametrize("path", FORMS)
async def test_required_fields_carry_the_asterisk_and_the_hint(client, db_session, admin_user, path):
    await _login(client, "testadmin")
    page = await client.get(f"{path}?lang=en")
    assert page.status_code == 200
    assert "Fields marked with * are required." in page.text
    parsed = Fields()
    parsed.feed(page.text)
    assert parsed.required_ids, path
    for field in parsed.required_ids:
        assert "fc-req" in parsed.labels.get(field, ""), (path, field)
    bg = await client.get(f"{path}?lang=bg")
    assert "са задължителни." in bg.text


@pytest.mark.asyncio
async def test_optional_fields_have_no_asterisk(client, db_session, admin_user):
    await _login(client, "testadmin")
    page = (await client.get("/vehicles/new?lang=en")).text
    parsed = Fields()
    parsed.feed(page)
    for optional in ("vin", "year", "color", "purchase_price"):
        assert "fc-req" not in parsed.labels[optional], optional
