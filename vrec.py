#!/usr/bin/env python3
"""Voice Recorder for Xubuntu/XFCE (GTK3 + GStreamer).

Features: mic picker, live level meter (works before you hit record),
pause/resume, recordings list with play / delete / open folder.
Saves WAV files to ~/Recordings.

Needs only things Xubuntu normally has: python3-gi, gir1.2-gstreamer-1.0,
gst-plugins-base/good. No pip, no sudo.
"""
import glob
import os
import re
import subprocess
import sys
import time
import wave

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
try:
    gi.require_version("Gst", "1.0")
    from gi.repository import Gst
except (ValueError, ImportError):
    sys.exit(
        "GStreamer Python bindings not found.\n"
        "Check: python3 -c \"import gi; gi.require_version('Gst','1.0'); "
        "from gi.repository import Gst\"\n"
        "Package needed: gir1.2-gstreamer-1.0 (ask your admin if you have no sudo)."
    )
from gi.repository import Gdk, GLib, Gtk

Gst.init(None)

SAVE_DIR = os.path.expanduser("~/Recordings")

CSS = b"""
.card {
    background-color: alpha(@theme_fg_color, 0.05);
    border-radius: 16px;
    padding: 18px;
}
.timer {
    font-size: 46px;
    font-weight: 300;
    font-family: monospace;
}
.dim { opacity: 0.6; font-size: 11px; }
.heading { font-weight: bold; font-size: 13px; }

progressbar.meter trough {
    min-height: 10px;
    border-radius: 6px;
    background-color: alpha(@theme_fg_color, 0.10);
    border: none;
}
progressbar.meter progress {
    min-height: 10px;
    border-radius: 6px;
    border: none;
    background-image: none;
    background-color: #2ecc71;
}
progressbar.meter.warn progress { background-color: #f5b301; }
progressbar.meter.hot progress { background-color: #ef5350; }

button.rec {
    min-width: 68px;
    min-height: 68px;
    padding: 0;
    border-radius: 999px;
    border: none;
    background-image: none;
    background-color: #e53935;
    color: white;
    box-shadow: 0 3px 10px alpha(#e53935, 0.45);
    transition: all 200ms ease;
}
button.rec:hover { background-color: #f0524f; }
button.rec.active { border-radius: 18px; background-color: #b71c1c; }

button.round {
    min-width: 46px;
    min-height: 46px;
    padding: 0;
    border-radius: 999px;
}
button.flat-icon {
    background-image: none;
    border: none;
    box-shadow: none;
    background-color: transparent;
    padding: 6px;
    border-radius: 8px;
}
button.flat-icon:hover { background-color: alpha(@theme_fg_color, 0.10); }
list.recordings { background-color: transparent; }
list.recordings row { border-radius: 10px; padding: 4px 6px; }
"""


def fmt(secs):
    secs = int(secs)
    return "%02d:%02d" % (secs // 60, secs % 60)


def human_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return "%.0f %s" % (n, unit) if unit == "B" else "%.1f %s" % (n, unit)
        n /= 1024.0
    return "%.1f TB" % n


def wav_duration(path):
    try:
        with wave.open(path, "rb") as w:
            return w.getnframes() / float(w.getframerate())
    except Exception:
        return 0.0


def list_mics():
    mon = Gst.DeviceMonitor.new()
    mon.add_filter("Audio/Source", None)
    mon.start()
    devices = mon.get_devices() or []
    mon.stop()
    out = []
    for d in devices:
        props = d.get_properties()
        if props and props.has_field("device.class"):
            if props.get_string("device.class") == "monitor":
                continue  # skip "Monitor of ..." loopback sources
        out.append(d)
    return out


def peak_db(structure):
    try:
        v = structure.get_value("peak")
        if v:
            return max(v)
    except Exception:
        pass
    m = re.search(r"peak=\(double\)\{\s*([^}]*)\}", structure.to_string())
    if m:
        try:
            return max(float(x) for x in m.group(1).split(","))
        except ValueError:
            pass
    return -100.0


class Recorder(Gtk.Window):
    def __init__(self):
        super().__init__(title="Voice Recorder")
        self.set_default_size(400, 660)
        self.connect("destroy", self.on_quit)

        # state
        self.state = "idle"  # idle | recording | paused
        self.pipe = None
        self.stopping = False
        self.path = ""
        self.elapsed = 0.0
        self.seg_start = 0.0
        self.level = 0.0
        self.devices = []
        self.populating = False
        self.player = None
        self.playing_path = None
        self.play_buttons = {}

        self.apply_css()
        self.build_ui()
        self.populate_devices()
        self.refresh_list()
        GLib.timeout_add(100, self.tick)
        self.start_monitor()

    # ---------------------------------------------------------------- UI
    def apply_css(self):
        prov = Gtk.CssProvider()
        prov.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def build_ui(self):
        header = Gtk.HeaderBar()
        header.set_show_close_button(True)
        header.set_title("Voice Recorder")
        folder_btn = Gtk.Button.new_from_icon_name(
            "folder-open-symbolic", Gtk.IconSize.BUTTON
        )
        folder_btn.set_tooltip_text("Open recordings folder")
        folder_btn.connect("clicked", self.on_open_folder)
        header.pack_end(folder_btn)
        self.set_titlebar(header)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        root.set_border_width(16)
        self.add(root)

        # --- recorder card
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        card.get_style_context().add_class("card")
        root.pack_start(card, False, False, 0)

        mic_row = Gtk.Box(spacing=6)
        mic_row.pack_start(Gtk.Label(label="Mic"), False, False, 0)
        self.combo = Gtk.ComboBoxText()
        self.combo.set_hexpand(True)
        self.combo.connect("changed", self.on_device_changed)
        mic_row.pack_start(self.combo, True, True, 0)
        self.refresh_btn = Gtk.Button.new_from_icon_name(
            "view-refresh-symbolic", Gtk.IconSize.BUTTON
        )
        self.refresh_btn.set_tooltip_text("Rescan microphones")
        self.refresh_btn.connect("clicked", lambda *_: self.on_rescan())
        mic_row.pack_start(self.refresh_btn, False, False, 0)
        card.pack_start(mic_row, False, False, 0)

        self.meter = Gtk.ProgressBar()
        self.meter.get_style_context().add_class("meter")
        card.pack_start(self.meter, False, False, 0)

        self.timer_label = Gtk.Label(label="00:00")
        self.timer_label.get_style_context().add_class("timer")
        card.pack_start(self.timer_label, False, False, 0)

        btn_row = Gtk.Box(spacing=22)
        btn_row.set_halign(Gtk.Align.CENTER)

        self.pause_btn = Gtk.Button()
        self.pause_btn.get_style_context().add_class("round")
        self.pause_img = Gtk.Image.new_from_icon_name(
            "media-playback-pause-symbolic", Gtk.IconSize.LARGE_TOOLBAR
        )
        self.pause_btn.add(self.pause_img)
        self.pause_btn.set_tooltip_text("Pause / resume")
        self.pause_btn.connect("clicked", self.on_pause)
        btn_row.pack_start(self.pause_btn, False, False, 0)

        self.rec_btn = Gtk.Button()
        self.rec_btn.get_style_context().add_class("rec")
        self.rec_img = Gtk.Image.new_from_icon_name(
            "media-record-symbolic", Gtk.IconSize.LARGE_TOOLBAR
        )
        self.rec_img.set_pixel_size(30)
        self.rec_btn.add(self.rec_img)
        self.rec_btn.set_tooltip_text("Record / stop")
        self.rec_btn.connect("clicked", self.on_rec)
        btn_row.pack_start(self.rec_btn, False, False, 0)

        # spacer so the record button stays centered
        spacer = Gtk.Box()
        spacer.set_size_request(46, 46)
        btn_row.pack_start(spacer, False, False, 0)
        card.pack_start(btn_row, False, False, 0)

        self.status = Gtk.Label(label="")
        self.status.get_style_context().add_class("dim")
        self.status.set_line_wrap(True)
        self.status.set_max_width_chars(44)
        card.pack_start(self.status, False, False, 0)

        # --- recordings list
        head = Gtk.Label(label="Recordings", xalign=0)
        head.get_style_context().add_class("heading")
        root.pack_start(head, False, False, 0)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.listbox.get_style_context().add_class("recordings")
        ph = Gtk.Label(label="No recordings yet")
        ph.get_style_context().add_class("dim")
        ph.set_margin_top(24)
        ph.show()
        self.listbox.set_placeholder(ph)
        scroller.add(self.listbox)
        root.pack_start(scroller, True, True, 0)

        self.update_ui_state()

    def update_ui_state(self):
        ctx = self.rec_btn.get_style_context()
        idle = self.state == "idle"
        if idle:
            ctx.remove_class("active")
            self.rec_img.set_from_icon_name(
                "media-record-symbolic", Gtk.IconSize.LARGE_TOOLBAR
            )
        else:
            ctx.add_class("active")
            self.rec_img.set_from_icon_name(
                "media-playback-stop-symbolic", Gtk.IconSize.LARGE_TOOLBAR
            )
        self.rec_img.set_pixel_size(30)
        self.pause_btn.set_sensitive(not idle)
        self.pause_img.set_from_icon_name(
            "media-playback-start-symbolic"
            if self.state == "paused"
            else "media-playback-pause-symbolic",
            Gtk.IconSize.LARGE_TOOLBAR,
        )
        self.combo.set_sensitive(idle)
        self.refresh_btn.set_sensitive(idle)

    def set_status(self, text):
        self.status.set_text(text)

    def set_meter(self, frac):
        frac = max(0.0, min(1.0, frac))
        self.meter.set_fraction(frac)
        ctx = self.meter.get_style_context()
        ctx.remove_class("warn")
        ctx.remove_class("hot")
        if frac > 0.88:
            ctx.add_class("hot")
        elif frac > 0.65:
            ctx.add_class("warn")

    # ----------------------------------------------------------- devices
    def populate_devices(self):
        self.populating = True
        self.combo.remove_all()
        self.combo.append_text("System default")
        self.devices = [None]
        try:
            for d in list_mics():
                self.combo.append_text(d.get_display_name())
                self.devices.append(d)
        except Exception as e:  # device monitor can be missing on odd setups
            self.set_status("Could not list mics: %s" % e)
        self.combo.set_active(0)
        self.populating = False

    def current_device(self):
        i = self.combo.get_active()
        if i is None or i < 0 or i >= len(self.devices):
            return None
        return self.devices[i]

    def on_device_changed(self, *_):
        if self.populating or self.state != "idle":
            return
        self.start_monitor()

    def on_rescan(self):
        self.populate_devices()
        self.start_monitor()

    # ---------------------------------------------------------- pipeline
    def make_pipeline(self, path=None):
        def make(name):
            el = Gst.ElementFactory.make(name)
            if el is None:
                raise RuntimeError("Missing GStreamer plugin: %s" % name)
            return el

        dev = self.current_device()
        src = dev.create_element(None) if dev else make("autoaudiosrc")
        if src is None:
            raise RuntimeError("Could not open that microphone")
        conv = make("audioconvert")
        res = make("audioresample")
        caps = make("capsfilter")
        caps.set_property(
            "caps", Gst.Caps.from_string("audio/x-raw,rate=44100,channels=1")
        )
        level = make("level")
        level.set_property("interval", 80 * 1000 * 1000)  # 80 ms
        level.set_property("post-messages", True)

        chain = [src, conv, res, caps, level]
        if path:
            chain.append(make("wavenc"))
            sink = make("filesink")
            sink.set_property("location", path)
        else:
            sink = make("fakesink")
            sink.set_property("sync", False)
            sink.set_property("async", False)
        chain.append(sink)

        pipe = Gst.Pipeline.new("voice")
        for el in chain:
            pipe.add(el)
        for a, b in zip(chain, chain[1:]):
            if not a.link(b):
                raise RuntimeError("Could not link audio pipeline")
        bus = pipe.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self.on_bus, pipe)
        return pipe

    def teardown(self):
        if self.pipe is not None:
            try:
                self.pipe.get_bus().remove_signal_watch()
            except Exception:
                pass
            self.pipe.set_state(Gst.State.NULL)
            self.pipe = None

    def start_monitor(self):
        """Idle pipeline: just feeds the level meter."""
        self.teardown()
        self.level = 0.0
        self.set_meter(0)
        try:
            pipe = self.make_pipeline(None)
        except RuntimeError as e:
            self.set_status(str(e))
            return
        self.pipe = pipe
        ret = pipe.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            self.teardown()
            self.set_status("Microphone not available. Check Settings > Sound > Input.")
        else:
            self.set_status("Ready - speak to test the meter")

    def on_bus(self, _bus, msg, pipe):
        if pipe is not self.pipe:
            return
        t = msg.type
        if t == Gst.MessageType.ELEMENT:
            s = msg.get_structure()
            if s is not None and s.get_name() == "level":
                db = peak_db(s)
                frac = max(0.0, min(1.0, (db + 50.0) / 50.0))
                if frac > self.level:
                    self.level = frac
                else:
                    self.level = self.level * 0.7 + frac * 0.3
                self.set_meter(self.level)
        elif t == Gst.MessageType.EOS:
            if self.stopping:
                self.finalize()
        elif t == Gst.MessageType.ERROR:
            err, _dbg = msg.parse_error()
            was_recording = self.state != "idle"
            self.teardown()
            self.state = "idle"
            self.stopping = False
            self.set_meter(0)
            self.update_ui_state()
            self.refresh_list()
            self.set_status(
                ("Recording stopped: " if was_recording else "Mic error: ")
                + err.message
            )

    # --------------------------------------------------------- recording
    def on_rec(self, *_):
        if self.state == "idle":
            self.start_recording()
        else:
            self.stop_recording()

    def start_recording(self):
        self.stop_playback()
        try:
            os.makedirs(SAVE_DIR, exist_ok=True)
        except OSError as e:
            self.set_status("Can't create %s: %s" % (SAVE_DIR, e))
            return
        self.teardown()
        self.path = os.path.join(SAVE_DIR, time.strftime("rec_%Y%m%d_%H%M%S.wav"))
        try:
            pipe = self.make_pipeline(self.path)
        except RuntimeError as e:
            self.set_status(str(e))
            self.start_monitor()
            return
        self.pipe = pipe
        ret = pipe.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            self.teardown()
            self.set_status("Could not start recording - mic unavailable.")
            return
        self.state = "recording"
        self.stopping = False
        self.elapsed = 0.0
        self.seg_start = time.time()
        self.timer_label.set_text("00:00")
        self.set_status("Recording...")
        self.update_ui_state()

    def on_pause(self, *_):
        if self.pipe is None:
            return
        if self.state == "recording":
            self.pipe.set_state(Gst.State.PAUSED)
            self.elapsed += time.time() - self.seg_start
            self.state = "paused"
            self.set_meter(0)
            self.set_status("Paused")
        elif self.state == "paused":
            self.pipe.set_state(Gst.State.PLAYING)
            self.seg_start = time.time()
            self.state = "recording"
            self.set_status("Recording...")
        self.update_ui_state()

    def stop_recording(self):
        if self.pipe is None or self.stopping:
            return
        if self.state == "recording":
            self.elapsed += time.time() - self.seg_start
        self.timer_label.set_text(fmt(self.elapsed))
        self.stopping = True
        self.pipe.set_state(Gst.State.PLAYING)  # EOS needs a running pipeline
        self.pipe.send_event(Gst.Event.new_eos())
        GLib.timeout_add(2500, self._force_finalize)

    def _force_finalize(self):
        if self.stopping:
            self.finalize()
        return False

    def finalize(self):
        if not self.stopping:
            return
        self.stopping = False
        self.teardown()
        self.state = "idle"
        saved = os.path.exists(self.path) and os.path.getsize(self.path) > 44
        self.update_ui_state()
        self.refresh_list()
        self.start_monitor()
        if saved:
            self.set_status("Saved: " + os.path.basename(self.path))
        else:
            self.set_status("Nothing recorded - check your mic input.")

    def tick(self):
        if self.state == "recording":
            self.timer_label.set_text(
                fmt(self.elapsed + time.time() - self.seg_start)
            )
        elif self.state == "paused":
            self.timer_label.set_text(fmt(self.elapsed))
        return True

    # ---------------------------------------------------- recordings list
    def refresh_list(self):
        for child in self.listbox.get_children():
            self.listbox.remove(child)
        self.play_buttons = {}
        files = glob.glob(os.path.join(SAVE_DIR, "*.wav"))
        files.sort(key=os.path.getmtime, reverse=True)
        for path in files:
            self.listbox.add(self.make_row(path))
        self.listbox.show_all()

    def make_row(self, path):
        row = Gtk.ListBoxRow()
        box = Gtk.Box(spacing=6)
        row.add(box)

        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        name = Gtk.Label(label=os.path.basename(path), xalign=0)
        name.set_ellipsize(3)  # END
        info.pack_start(name, False, False, 0)
        st = os.stat(path)
        sub = "%s  ·  %s  ·  %s" % (
            fmt(wav_duration(path)),
            human_size(st.st_size),
            time.strftime("%d %b %H:%M", time.localtime(st.st_mtime)),
        )
        sublabel = Gtk.Label(label=sub, xalign=0)
        sublabel.get_style_context().add_class("dim")
        info.pack_start(sublabel, False, False, 0)
        box.pack_start(info, True, True, 0)

        play = Gtk.Button()
        play.get_style_context().add_class("flat-icon")
        img = Gtk.Image.new_from_icon_name(
            "media-playback-start-symbolic", Gtk.IconSize.BUTTON
        )
        play.add(img)
        play.set_tooltip_text("Play / stop")
        play.connect("clicked", lambda *_: self.toggle_play(path))
        box.pack_start(play, False, False, 0)
        self.play_buttons[path] = img

        delete = Gtk.Button()
        delete.get_style_context().add_class("flat-icon")
        delete.add(
            Gtk.Image.new_from_icon_name("user-trash-symbolic", Gtk.IconSize.BUTTON)
        )
        delete.set_tooltip_text("Delete")
        delete.connect("clicked", lambda *_: self.confirm_delete(path))
        box.pack_start(delete, False, False, 0)
        return row

    def confirm_delete(self, path):
        dlg = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.CANCEL,
            text="Delete this recording?",
        )
        dlg.format_secondary_text(os.path.basename(path))
        dlg.add_button("Delete", Gtk.ResponseType.OK)
        resp = dlg.run()
        dlg.destroy()
        if resp == Gtk.ResponseType.OK:
            if self.playing_path == path:
                self.stop_playback()
            try:
                os.remove(path)
            except OSError as e:
                self.set_status("Delete failed: %s" % e)
            self.refresh_list()

    def on_open_folder(self, *_):
        os.makedirs(SAVE_DIR, exist_ok=True)
        try:
            subprocess.Popen(["xdg-open", SAVE_DIR])
        except OSError as e:
            self.set_status("Could not open folder: %s" % e)

    # ---------------------------------------------------------- playback
    def toggle_play(self, path):
        was = self.playing_path
        self.stop_playback()
        if was == path or self.state != "idle":
            return
        player = Gst.ElementFactory.make("playbin")
        if player is None:
            self.set_status("Playback needs gst-plugins-base (playbin).")
            return
        fake = Gst.ElementFactory.make("fakesink")
        if fake is not None:
            player.set_property("video-sink", fake)
        player.set_property("uri", GLib.filename_to_uri(path, None))
        bus = player.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self.on_player_bus, player)
        self.player = player
        self.playing_path = path
        player.set_state(Gst.State.PLAYING)
        self.update_play_icons()

    def on_player_bus(self, _bus, msg, player):
        if player is not self.player:
            return
        if msg.type in (Gst.MessageType.EOS, Gst.MessageType.ERROR):
            if msg.type == Gst.MessageType.ERROR:
                err, _ = msg.parse_error()
                self.set_status("Playback error: " + err.message)
            self.stop_playback()

    def stop_playback(self):
        if self.player is not None:
            try:
                self.player.get_bus().remove_signal_watch()
            except Exception:
                pass
            self.player.set_state(Gst.State.NULL)
            self.player = None
        self.playing_path = None
        self.update_play_icons()

    def update_play_icons(self):
        for path, img in self.play_buttons.items():
            name = (
                "media-playback-stop-symbolic"
                if path == self.playing_path
                else "media-playback-start-symbolic"
            )
            img.set_from_icon_name(name, Gtk.IconSize.BUTTON)

    # -------------------------------------------------------------- quit
    def on_quit(self, *_):
        self.stop_playback()
        if self.pipe is not None and self.state != "idle":
            # finish the WAV header properly before exiting
            bus = self.pipe.get_bus()
            bus.remove_signal_watch()
            self.pipe.set_state(Gst.State.PLAYING)
            self.pipe.send_event(Gst.Event.new_eos())
            bus.timed_pop_filtered(
                2 * Gst.SECOND, Gst.MessageType.EOS | Gst.MessageType.ERROR
            )
        self.teardown()
        Gtk.main_quit()


if __name__ == "__main__":
    win = Recorder()
    win.show_all()
    Gtk.main()
