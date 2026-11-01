"""QA #11: stored messages are shown in the viewer's current language, not the language they were created in."""

from types import SimpleNamespace

from app.services.notification_text import notification_text


def _note(**kw):
    base = dict(title="Stored title", message="Stored message", title_key=None, message_key=None, params=None)
    return SimpleNamespace(**{**base, **kw})


def test_keyed_notification_is_rendered_in_the_requested_language():
    note = _note(
        title_key="driver_doc.notify_pending_title",
        message_key="driver_doc.notify_pending_message",
        params={"driver": "Иван Тестов", "type": {"t": "driver_document_type.medical_certificate"}},
    )
    assert notification_text(note, "en") == (
        "Document change to approve",
        "Иван Тестов submitted a change to: Medical certificate.",
    )
    bg_title, bg_message = notification_text(note, "bg")
    assert bg_title == "Промяна в документ за одобрение"
    assert "Иван Тестов" in bg_message and "Medical certificate" not in bg_message


def test_a_note_from_the_manager_is_appended_untranslated():
    note = _note(
        title_key="driver_doc.notify_revision_title",
        message_key="driver_doc.notify_revision_message",
        params={"type": {"t": "driver_document_type.id_card"}, "note": "Photo is unreadable"},
    )
    assert notification_text(note, "en")[1].endswith(" Photo is unreadable")
    assert notification_text(note, "bg")[1].endswith(" Photo is unreadable")


def test_old_notifications_without_a_key_keep_their_stored_text():
    assert notification_text(_note(), "bg") == ("Stored title", "Stored message")
    assert notification_text(_note(), "en") == ("Stored title", "Stored message")
