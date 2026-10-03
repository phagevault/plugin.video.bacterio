import time
from threading import Thread
from typing import Any, Literal, NamedTuple

import xbmc
import xbmcgui
import xbmcplugin
from menu_items.episode import EpisodeItem
from menu_items.movie import MovieItem
from services import scraper
from services.tmdb import Tmdb
from services.torbox import TorBox
from settings.settings import get_setting
from utils.logger import debug, err, warn
from utils.notifications import error
from utils.types import (
    EpisodeScrapePayload,
    MovieScrapePayload,
    ScrapePayload,
    SourceResult,
)

_SCRAPE_TIMEOUT = 20
_POLL_INTERVAL = 500  # ms
_TB_DOWNLOAD_WAIT = 60  # seconds to wait for TorBox download
_TB_ERROR_STATES = {"error", "failed", "dead"}


_QUALITY_RANK = {
    "PREFERRED": 0,
    "4K": 1,
    "1080p": 2,
    "720p": 3,
    "SD": 4,
    "CAM": 5,
    "TELE": 5,
    "SYNC": 5,
}
_LANG_RANK = {
    "PREFERRED": 0,
    "EN": 1,
}


class ScrapeCancelled(Exception):
    """Raised when the user cancels the progress dialog while sources are still coming in."""


def _tb_ok() -> bool:
    return TorBox.is_enabled and TorBox.is_authenticated


def _sort_sources(sources: list[SourceResult], cached: set[str]) -> list[SourceResult]:
    quality_pref = get_setting("playback.preferred_quality")
    return sorted(
        sources,
        key=lambda s: (
            0 if s.get("hash", "").lower() in cached else 1,
            -float(s.get("size", 0)),
            _QUALITY_RANK.get(
                "PREFERRED"
                if s.get("quality", "SD") == quality_pref
                else s.get("quality", "SD"),
                9,
            ),
            -int(s.get("seeders") or 0),
        ),
    )


def _add_to_torbox(
    progress: xbmcgui.DialogProgress,
    magnet: str,
    title: str,
    season: int | None = None,
    episode: int | None = None,
    is_cached: bool = False,
) -> str:
    debug(f"{title}, {season}x{episode} cached:{is_cached}", "_add_to_torbox")

    try:
        result = TorBox.add(magnet, is_cached)
        debug(f"{result}", "add_magnet")
        if not result.get("success"):
            warn(f"TorBox add_magnet failed: {result}", "_add_to_torbox")
            warn(f"{magnet}", "_add_to_torbox")
            return ""

        data = result.get("data")
        if data is None:
            return ""

        torrent_id: int = data.get("torrent_id")
        if not torrent_id:
            return ""

        status_data: dict[str, Any] = {}
        deadline = time.monotonic() + _TB_DOWNLOAD_WAIT
        while time.monotonic() < deadline:
            xbmc.sleep(_POLL_INTERVAL)
            if progress.iscanceled():
                progress.close()
                return "Cancel"
            torrent_status = TorBox.get_torrent_status(torrent_id)
            debug(f"{torrent_status}", "wait_for_download")
            status_data = torrent_status.get("data", {})
            state: str = status_data.get("download_state", "")
            pct = int(float(status_data.get("progress", 0)) * 100)
            progress.update(pct, f"TorBox: {state}")
            if state in _TB_ERROR_STATES:
                warn(f"TorBox error state: {state}", "_add_to_torbox")
                return ""
            if state in ("cached", "completed", "uploading", "seeding"):
                break
        else:
            warn("TorBox timed out", "_add_to_torbox")
            return ""

        files: list[dict[str, Any]] = status_data.get("files") or []
        target = TorBox.pick_video_file(files, season=season, episode=episode)
        if not target:
            warn("Target not found", "target")
            return ""

        file_id: int | None = target.get("id")
        if file_id is None:
            warn("FileId not found", "file_id")
            warn(f"{target}", "file_id")
            return ""

        dl_result = TorBox.request_download(torrent_id, file_id)
        if not dl_result.get("success"):
            warn(f"{dl_result.get('error')}", "dl_result")
            return ""
        url = dl_result.get("data", "")
        return url if isinstance(url, str) else ""

    except Exception as e:
        err(str(e), "_add_to_torbox")
        return ""


def _tag_playing(item_type: str, tmdb_id: str, season: str, episode: str) -> None:
    debug(f"type={item_type} id={tmdb_id} season={season} ep={episode}", "_tag_playing")
    win = xbmcgui.Window(10000)
    win.setProperty("bacterio.type", item_type)
    win.setProperty("bacterio.tmdb_id", tmdb_id)
    win.setProperty("bacterio.season", season)
    win.setProperty("bacterio.episode", episode)


# ── Scraping ───────────────────────────────────────────────────────────────


class _TitleInfo(NamedTuple):
    title: str
    year: str
    imdb_id: str


def _build_play_item(
    item_type: str, tmdb_id: str, season: str, episode: str
) -> tuple[xbmcgui.ListItem, _TitleInfo]:
    """Rebuild the same ListItem the listing built, from the same TMDB calls.

    Since this hits the same cached TMDB responses the listing just fetched,
    it avoids both a second network round trip and hand-rolled field copying.
    """
    if item_type == "episode":
        show_details = Tmdb.tv_show_details(int(tmdb_id))
        season_details = Tmdb.tv_season(int(tmdb_id), int(season))
        item = EpisodeItem(int(episode), season_details, show_details)
        ext: dict[str, Any] = show_details.get("external_ids") or {}
        title = show_details.get("name", "")
        year = (show_details.get("first_air_date") or "")[:4]
        imdb_id = str(ext.get("imdb_id") or "")
    else:
        details = Tmdb.movie_rich_details(int(tmdb_id))
        item = MovieItem(details)
        ext = details.get("external_ids") or {}
        title = details.get("title", "")
        year = (details.get("release_date") or "")[:4]
        imdb_id = str(ext.get("imdb_id") or details.get("imdb_id") or "")
    return item.listItem, _TitleInfo(title=title, year=year, imdb_id=imdb_id)


def _build_payload(
    meta: _TitleInfo, item_type: str, season: str, episode: str
) -> ScrapePayload:
    """Build the payload Magneto's providers expect for a scrape() call."""
    if item_type == "episode":
        return EpisodeScrapePayload(
            tvshowtitle=meta.title,
            title=meta.title,
            year=meta.year,
            imdb=meta.imdb_id,
            season=int(season),
            episode=int(episode),
            aliases=[],
        )
    return MovieScrapePayload(
        title=meta.title, year=meta.year, imdb=meta.imdb_id, aliases=[]
    )


def get_sources(
    payload: ScrapePayload,
    progress: xbmcgui.DialogProgress,
    timeout: int = _SCRAPE_TIMEOUT,
) -> list[SourceResult]:
    """Run the Magneto scrape in a background thread while driving *progress*.

    Raises ScrapeCancelled if the user cancels before the scrape completes.
    """
    sources: list[SourceResult] = []
    scrape_thread = Thread(
        target=lambda: scraper.scrape(payload, timeout, on_result=sources.append),
        daemon=True,
    )
    scrape_thread.start()

    start = time.monotonic()
    while scrape_thread.is_alive():
        elapsed = time.monotonic() - start
        pct = min(int((elapsed / timeout) * 100), 99)
        progress.update(pct, f"Found {len(sources)} sources…")
        if progress.iscanceled():
            raise ScrapeCancelled()
        xbmc.sleep(300)

    progress.update(100)
    return sources


def _check_availability(hashes: list[str], use_tb: bool) -> set[str]:
    if not use_tb:
        return set()
    try:
        return TorBox.check_instant_availability(hashes)
    except Exception as e:
        err(str(e), "_check_availability")
        return set()


# ── Playback ─────────────────────────────────────────────────────────────────

_TryOutcome = Literal["played", "cancelled", "exhausted"]


def _try_sources(
    ordered: list[SourceResult],
    cached: set[str],
    item_type: str,
    tmdb_id: str,
    handle: int,
    season: str,
    episode: str,
    title: str,
    progress: xbmcgui.DialogProgress,
    listItem: xbmcgui.ListItem,
) -> _TryOutcome:
    """Try each source in order, falling back to the next on failure."""
    ep_season = int(season) if season else None
    ep_episode = int(episode) if episode else None

    for i, src in enumerate(ordered):
        progress.update(0, f"Trying source {i + 1}/{len(ordered)}\n{src['name']}")
        h = src["hash"]
        magnet = src["url"]
        debug(str(src), "try_source")

        is_cached = h in cached

        direct_url = _add_to_torbox(
            progress,
            magnet,
            title,
            season=ep_season,
            episode=ep_episode,
            is_cached=is_cached,
        )
        if direct_url and direct_url != "Cancel":
            _tag_playing(item_type, tmdb_id, season, episode)
            listItem.setPath(direct_url)
            listItem.setContentLookup(False)
            progress.update(100, "Success.\nOpening...")
            progress.close()
            debug(direct_url, "play_url")
            xbmcplugin.setResolvedUrl(handle, True, listItem)
            return "played"
        if direct_url == "Cancel":
            progress.close()
            return "cancelled"
        progress.close()

    return "exhausted"


# ── Public API ────────────────────────────────────────────────────────────────


def resolve_and_play(
    item_type: str,
    tmdb_id: str,
    handle: int,
    season: str = "",
    episode: str = "",
    force_select: bool = False,
):
    def _fail(msg: str):
        err(msg, "resolve_and_play")
        error(msg)
        xbmcplugin.setResolvedUrl(handle, False, xbmcgui.ListItem())

    use_tb = _tb_ok()
    debug(
        f"use_tb={use_tb} type={item_type} id={tmdb_id} s={season} e={episode}",
        "resolve_and_play",
    )
    if not use_tb:
        _fail("No debrid service configured. Add TorBox in Settings.")
        return

    try:
        listItem, meta = _build_play_item(item_type, tmdb_id, season, episode)
    except Exception as e:
        err(str(e), "resolve_and_play/tmdb")
        _fail("Could not fetch metadata from TMDB.")
        return

    debug(
        f"title={meta.title!r} year={meta.year} imdb={meta.imdb_id}", "resolve_and_play"
    )
    if not meta.imdb_id:
        _fail("No IMDB ID found for this title.")
        return

    payload = _build_payload(meta, item_type, season, episode)

    progress = xbmcgui.DialogProgress()
    progress.create("Bacterio", f"Searching - {meta.title}…")

    try:
        sources = get_sources(payload, progress)
    except ScrapeCancelled:
        progress.close()
        _fail("Cancelled.")
        return

    if not sources:
        progress.close()
        _fail(f"No sources found for {meta.title}.")
        return

    hashes = list({s["hash"] for s in sources})
    cached = _check_availability(hashes, use_tb)

    sorted_src = _sort_sources(sources, cached)
    auto_play = not force_select and get_setting("playback.auto_play") == "true"

    if auto_play:
        ordered = sorted_src
    else:
        source = _select_source(sorted_src, cached, meta.title)
        if source is None:
            progress.close()
            xbmcplugin.setResolvedUrl(handle, False, xbmcgui.ListItem())
            return
        ordered = [source]

    outcome = _try_sources(
        ordered,
        cached,
        item_type,
        tmdb_id,
        handle,
        season,
        episode,
        meta.title,
        progress,
        listItem,
    )
    if outcome == "cancelled":
        _fail("Cancelled")
    elif outcome == "exhausted":
        _fail(f"All {len(ordered)} sources failed for {meta.title}.")


def _select_source(
    sources: list[SourceResult],
    cached: set[str],
    title: str = "",
) -> SourceResult | None:
    labels: list[str] = []
    for s in sources:
        h = s.get("hash", "").lower()
        is_cached = h in cached
        tag = "[Cached] " if is_cached else ""
        package = s.get("package")
        if package == "show":
            tag += "[Show Pack] "
        elif package == "season":
            tag += "[Pack] "
        quality = s.get("quality", "?")
        size = s.get("size")
        size_str = f"  {size:.1f} GB"
        seeders = s.get("seeders")
        seed_str = f"  Seeders: {seeders}" if seeders and not is_cached else ""
        name = (s.get("name") or "")[:55]
        labels.append(f"{quality}{size_str}{seed_str}  {name} - {tag}")

    idx = xbmcgui.Dialog().select(f"Sources - {title}", list(labels))
    if idx < 0:
        return None
    return sources[idx]
