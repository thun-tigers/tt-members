"""Verifiziert, dass tt-members das geteilte Layout aus tt-common rendert."""
from flask import render_template_string


class FakeUser:
    username = "maxmuster"
    display_name = "Max Muster"
    role = "user"


CHILD = '{% extends "base.html" %}{% block content %}<p id="c">x</p>{% endblock %}'


def test_eingeloggt_rendert_members_layout(app):
    with app.test_request_context("/"):
        html = render_template_string(CHILD, current_user=FakeUser())
    # geteiltes Layout aktiv
    assert "/tt-common-static/js/table_enhancements.js" in html
    assert 'id="themeToggle"' in html
    # Members-spezifische Nav
    assert "Übersicht" in html
    assert "Messages" in html
    # Logout ist ein POST-Formular (nicht GET-Link)
    assert 'method="POST"' in html
    assert 'action="/logout"' in html
    assert "<p id=\"c\">x</p>" in html


def test_ausgeloggt_rendert_minimal_bar(app):
    with app.test_request_context("/"):
        html = render_template_string(CHILD, current_user=None)
    assert "Anmelden" in html
    assert "Übersicht" not in html
