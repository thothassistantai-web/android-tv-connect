"""About dialog."""

from __future__ import annotations

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, Gtk

from .branding import (
    APP_NAME,
    APP_TAGLINE,
    COPYRIGHT,
    ICON_NAME,
    ISSUE_TRACKER,
    VERSION,
    WEBSITE,
)


def show_about_dialog(parent: Gtk.Window | None) -> None:
    about = Adw.AboutWindow(transient_for=parent, modal=True)
    about.set_application_name(APP_NAME)
    about.set_application_icon(ICON_NAME)
    about.set_version(VERSION)
    about.set_comments(APP_TAGLINE)
    about.set_copyright(COPYRIGHT)
    about.set_website(WEBSITE)
    about.set_issue_url(ISSUE_TRACKER)
    about.set_developers(["Android TV Connect contributors"])
    if hasattr(about, "set_technology_version"):
        about.set_technology_version("GTK 4 · libadwaita · GStreamer")
    about.present()
