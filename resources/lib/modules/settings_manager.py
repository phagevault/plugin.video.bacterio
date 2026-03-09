# -*- coding: utf-8 -*-
"""
settings_manager.py  →  modules/settings_manager.py

Thin wrapper around Kodi's native addon settings (xbmcaddon).
Replaces caches/settings_cache.py entirely.

Kodi owns persistence — values are stored/read directly from the addon's
settings.xml in addon_data.  Window(10000) properties are populated at startup
and kept in sync on every write so that skin XML
($INFO[Window(10000).Property(bacterio.*)]) continues to work unchanged.
"""

import xbmcaddon
import xbmcgui

_addon  = xbmcaddon.Addon()
_window = xbmcgui.Window(10000)

_PREFIX = 'bacterio.'


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _sid(setting_id):
    """Strip the 'bacterio.' prefix so xbmcaddon always receives a bare id."""
    if setting_id and setting_id.startswith(_PREFIX):
        return setting_id[len(_PREFIX):]
    return setting_id


def _prop(setting_id):
    """Return the Window(10000) property key for a given setting id."""
    return '%s%s' % (_PREFIX, _sid(setting_id))


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def get_setting(setting_id, fallback=''):
    """
    Read a setting. Checks Window(10000) first (fast path after warm-up),
    then falls back to xbmcaddon (disk read). Always returns a string.
    Accepts ids with or without the 'bacterio.' prefix.
    """
    sid    = _sid(setting_id)
    cached = _window.getProperty(_prop(sid))
    if cached:
        return cached
    value = _addon.getSetting(sid)
    if value:
        _window.setProperty(_prop(sid), value)
        return value
    return fallback


def set_setting(setting_id, value):
    """
    Persist a setting via xbmcaddon and mirror to Window(10000).
    Always pass value as a string — Kodi coerces to the declared type.
    Accepts ids with or without the 'bacterio.' prefix.
    """
    sid = _sid(setting_id)
    _addon.setSetting(sid, str(value))
    _window.setProperty(_prop(sid), str(value))


# ─────────────────────────────────────────────────────────────────────────────
# Startup cache warm-up
# ─────────────────────────────────────────────────────────────────────────────

# Every setting id that should be pre-loaded into Window(10000) on service
# start.  This is the full list from default_settings() in settings_cache.py.
# Defaults are now declared in resources/settings.xml <default> tags — this
# list only controls what gets cached in memory at boot.
_WARMUP_SETTINGS = [
    # General
    'auto_start_bacterio', 'addon_fanart', 'limit_concurrent_threads',
    'max_threads', 'watched_indicators', 'trakt.sync_interval',
    'trakt.refresh_widgets', 'datetime.offset',
    'movie_download_directory', 'tvshow_download_directory',
    'premium_download_directory', 'image_download_directory',
    # Features
    'extras.enable_extra_ratings', 'extras.enable_scrollbars',
    'media_open_action_movie', 'media_open_action_tvshow',
    'ai_model.order', 'ai_model.limit',
    # Content
    'paginate.lists', 'paginate.limit_addon', 'paginate.limit_widgets',
    'paginate.jump_to', 'ignore_articles', 'recommend_service', 'recommend_seed',
    'mpaa_region', 'lists_cache_duraton', 'tv_progress_location', 'show_specials',
    'use_season_name', 'default_all_episodes', 'avoid_episode_spoilers',
    'include_anime_tvshow', 'show_unaired_watchlist', 'meta_filter',
    'use_viewtypes', 'manual_viewtypes',
    'view.main', 'view.movies', 'view.tvshows', 'view.seasons',
    'view.episodes', 'view.episodes_single', 'view.premium',
    'sort.progress', 'sort.watched', 'sort.collection', 'sort.watchlist',
    'tmdblist.list_sort', 'personal_list.list_sort',
    'personal_list.show_author', 'personal_list.sort_unseen_to_top',
    'personal_list.highlight_unseen', 'personal_list.unseen_highlight',
    'widget_refresh_timer', 'widget_refresh_notification',
    'widget_hide_watched', 'widget_hide_next_page',
    'rpdb_enabled', 'context_menu.order',
    # Single episode lists
    'single_ep_display', 'single_ep_display_widget', 'single_ep_unwatched_episodes',
    'nextep.method', 'nextep.sort_type', 'nextep.sort_order',
    'nextep.limit_history', 'nextep.limit', 'nextep.include_unwatched',
    'nextep.include_airdate', 'nextep.airing_today', 'nextep.include_unaired',
    'trakt.flatten_episodes', 'trakt.calendar_sort_order',
    'trakt.calendar_previous_days', 'trakt.calendar_future_days',
    # Meta accounts
    'trakt.user', 'trakt.client', 'trakt.secret', 'trakt.token',
    'trakt.expires', 'trakt.refresh', 'trakt.next_daily_clear',
    'tmdb_api', 'tmdb.token', 'tmdb.account_id',
    'omdb_api', 'rpdb_api', 'google_api', 'groq_api',
    # Sources / accounts
    'provider.external', 'external_scraper.name', 'external_scraper.module',
    'external.cache_check', 'external.filter_sources',
    'rd.enabled', 'rd.token', 'rd.account_id',
    'store_resolved_to_cloud.real-debrid',
    'provider.rd_cloud', 'rd_cloud.title_filter',
    'check.rd_cloud', 'autoplay.rd_cloud',
    'results.sort_rdcloud_first', 'rd.priority', 'rd.alternate_base_url',
    'pm.enabled', 'pm.token', 'pm.account_id',
    'store_resolved_to_cloud.premiumize.me',
    'provider.pm_cloud', 'pm_cloud.title_filter',
    'check.pm_cloud', 'autoplay.pm_cloud',
    'results.sort_pmcloud_first', 'pm.priority',
    'ad.enabled', 'ad.token', 'ad.account_id',
    'store_resolved_to_cloud.alldebrid',
    'provider.ad_cloud', 'ad_cloud.title_filter',
    'check.ad_cloud', 'autoplay.ad_cloud',
    'results.sort_adcloud_first', 'ad.priority',
    'ed.enabled', 'ed.token', 'ed.priority',
    'tb.enabled', 'tb.token',
    'store_resolved_to_cloud.torbox',
    'provider.tb_cloud', 'tb_cloud.title_filter',
    'check.tb_cloud', 'autoplay.tb_cloud',
    'results.sort_tbcloud_first', 'tb.priority',
    'provider.easynews', 'easynews_user', 'easynews_password',
    'easynews.title_filter', 'easynews.filter_lang', 'easynews.lang_filters',
    'check.easynews', 'autoplay.easynews', 'en.priority',
    'provider.folders', 'folders.title_filter',
    'check.folders', 'autoplay.folders',
    'results.sort_folders_first', 'results.folders_ignore_filters', 'folders.priority',
    'folder1.display_name', 'folder1.movies_directory', 'folder1.tv_shows_directory',
    'folder2.display_name', 'folder2.movies_directory', 'folder2.tv_shows_directory',
    'folder3.display_name', 'folder3.movies_directory', 'folder3.tv_shows_directory',
    'folder4.display_name', 'folder4.movies_directory', 'folder4.tv_shows_directory',
    'folder5.display_name', 'folder5.movies_directory', 'folder5.tv_shows_directory',
    # Results
    'results.timeout', 'results.list_format',
    'results.auto_rescrape_cache_ignored', 'results.auto_rescrape_imdb_year',
    'results.auto_rescrape_with_all', 'results.auto_episode_group',
    'results.ignore_filter', 'results.sort_order', 'results.sort_order_display',
    'results.filter_size_method', 'results.line_speed',
    'results.movie_size_max', 'results.episode_size_max',
    'results.movie_size_min', 'results.episode_size_min',
    'results.size_unknown', 'results.size_sort_weighted',
    'results.size_sort_direction', 'results.limit_number_quality',
    'results.limit_number_total', 'results.include.unknown.size',
    'filter.include_prerelease', 'filter.3d', 'filter.hdr', 'filter.dv',
    'filter.av1', 'filter.enhanced_upscaled', 'filter.hevc',
    'filter.hevc.max_quality', 'filter.hevc.max_autoplay_quality',
    'filter.sort_to_top', 'filter.preferred_filters', 'filter_audio',
    'highlight.type',
    'provider.rd_highlight', 'provider.pm_highlight', 'provider.ad_highlight',
    'provider.oc_highlight', 'provider.ed_highlight', 'provider.tb_highlight',
    'provider.easynews_highlight', 'provider.debrid_cloud_highlight',
    'provider.folders_highlight',
    'scraper_4k_highlight', 'scraper_1080p_highlight',
    'scraper_720p_highlight', 'scraper_SD_highlight', 'scraper_single_highlight',
    # Playback
    'auto_play_movie', 'results_quality_movie', 'autoplay_quality_movie',
    'auto_resume_movie', 'stinger_alert.show', 'stinger_alert.window_percentage',
    'stinger_alert.use_chapters',
    'auto_play_episode', 'results_quality_episode', 'autoplay_quality_episode',
    'autoplay_next_episode', 'autoplay_alert_method', 'autoplay_default_action',
    'autoplay_next_window_percentage', 'autoplay_use_chapters',
    'autoplay_watching_check', 'autoscrape_next_episode',
    'autoscrape_next_window_percentage', 'autoscrape_use_chapters',
    'auto_resume_episode',
    'playback.limit_resolve', 'easynews.playback_method',
    'easynews.playback_method_limited', 'playback.volumecheck_enabled',
    'playback.volumecheck_percent', 'playback.auto_enable_subs',
    # Misc hidden
    'reuse_language_invoker',
    'extras.enabled',
    'extras.tvshow.button10', 'extras.tvshow.button11', 'extras.tvshow.button12',
    'extras.tvshow.button13', 'extras.tvshow.button14', 'extras.tvshow.button15',
    'extras.tvshow.button16', 'extras.tvshow.button17',
    'extras.movie.button10', 'extras.movie.button11', 'extras.movie.button12',
    'extras.movie.button13', 'extras.movie.button14', 'extras.movie.button15',
    'extras.movie.button16', 'extras.movie.button17',
]


def warm_up_memory_cache():
    """
    Call once from service.py on Kodi start.
    Reads every setting from xbmcaddon and pushes it into Window(10000) so
    that skin XML ($INFO[Window(10000).Property(bacterio.*)]) has instant
    access without hitting disk on every condition check.
    """
    for sid in _WARMUP_SETTINGS:
        try:
            value = _addon.getSetting(sid)
            _window.setProperty(_prop(sid), value)
        except Exception:
            pass
