import time
import requests
from typing import Any

from services.types import AuthData, PollStatus, Service
from settings.ids import SettingID as SID
from utils.logger import debug, err, log, warn

import re

_VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".mov", ".m4v", ".ts", ".wmv"}


class TorBoxAPI(Service):
    @property
    def is_authenticated(self) -> bool:
        if not self.access_token:
            self.access_token = self._get_setting(SID.ACCESS_TOKEN)
        return bool(self.access_token)

    def __init__(self):
        self.app_id: str = self._get_setting(SID.APP_ID)
        self.client_id: str = self._get_setting(SID.CLIENT_ID)
        self.client_secret: str = self._get_setting(SID.CLIENT_SECRET)
        self.access_token: str = self._get_setting(SID.ACCESS_TOKEN)
        self.refresh_token: str = self._get_setting(SID.REFRESH_TOKEN)

        self.base_url: str = "https://api.torbox.app/v1/api"
        self.device_code: str = ""
        self.user_code: str = ""
        self.refresh_retries: int = 0
        self.break_auth_loop: bool = False

    @property
    def setting_prefix(self) -> str:
        return "tb"

    def start_auth(self) -> AuthData:
        response = self._get("user/auth/device/start?app=Bacterio")
        data = response["data"]
        self.device_code = data["device_code"]
        self.user_code = data["code"]
        self.expiry: int = int(time.monotonic() + 600)

        return {
            "device_code": self.device_code,
            "direct_verification_url": data["verification_url"],
            "expiry": 600,
            "user_code": self.user_code,
            "interval": int(data["interval"]),
            "verification_url": data["verification_url"],
        }

    def poll(self) -> PollStatus:
        response = requests.post(
            f"{self.base_url}/user/auth/device/token",
            json={"device_code": self.device_code},
            timeout=20,
        )
        data = response.json()["data"]

        match response.status_code:
            case 200:
                debug(f"{response.json()}")
                if response.json()["success"]:
                    debug(f"{data}")
                    self.access_token = data["access_token"]
                    return PollStatus.SUCCESS

            case 400:
                if time.monotonic() >= self.expiry:
                    return PollStatus.EXPIRED

                return PollStatus.PENDING
            case _:
                return PollStatus.ERROR

        return PollStatus.ERROR

    def auth_complete(self) -> None:
        if self.access_token:
            self._set_setting(SID.ACCESS_TOKEN, self.access_token)

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}"}

    def _get(
        self,
        endpoint: str,
        params: dict[str, Any] | list[tuple[str, str]] | None = None,
    ) -> dict[str, Any]:
        response = requests.get(
            f"{self.base_url}/{endpoint}",
            headers=self._headers,
            params=params or {},
            timeout=20,
        )
        response.raise_for_status()
        return response.json()

    def _post(
        self,
        endpoint: str,
        data: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = requests.post(
            f"{self.base_url}/{endpoint}",
            headers=self._headers,
            data=data,
            json=json,
            timeout=20,
        )
        response.raise_for_status()
        return response.json() if response.content else {}

    def test_connection(self) -> bool:
        try:
            result = self._get("user/me")
            return bool(result.get("success"))
        except Exception:
            return False

    def add_magnet(self, magnet: str, skip_cached: bool = False) -> dict[str, Any]:
        result = self._post(
            "torrents/createtorrent",
            {
                "magnet": magnet,
                "seed": "3",
                "add_only_if_cached": not skip_cached,
            },
        )

        if result.get("success"):
            return result

        hash_val = _extract_hash(magnet)
        if hash_val:
            existing = self.find_torrent_by_hash(hash_val)
            if existing:
                torrent_id = existing.get("id")
                return {"success": True, "data": {"torrent_id": torrent_id}}
        return result

    def get_torrent_info(self, magnet: str) -> dict[str, Any]:
        return self._post(
            "torrents/torrentinfo",
            data={
                "magnet": magnet,
                "use_cache_lookup": True,
                "peers_only": False,
            },
        )

    def get_torrent_status(self, torrent_id: int) -> dict[str, Any]:
        return self._get(
            "torrents/mylist",
            params={
                "id": torrent_id,
                "bypass_cache": True,
            },
        )

    def add(self, magnet: str, is_cached: bool = False) -> dict[str, Any]:
        if not is_cached:
            info = self.get_torrent_info(magnet)
            if info.get("success"):
                data: dict[str, Any] = info.get("data", {})
                seeds: int = data.get("seeds", 0)
                if seeds <= 0:
                    warn("seeds are too low", "tb.add")
                    return {"success": False}
            else:
                err("couldn't get info", "tb.add")
                return {"success": False}

        return self.add_magnet(magnet, not is_cached)

    def request_download(self, torrent_id: int, file_id: int) -> dict[str, Any]:
        return self._get(
            "torrents/requestdl",
            {
                "token": self.access_token,
                "torrent_id": str(torrent_id),
                "file_id": str(file_id),
                "zip_link": False,
            },
        )

    def find_torrent_by_hash(self, hash_str: str) -> dict[str, Any] | None:
        try:
            result = self._get("torrents/mylist", {"bypass_cache": "true"})
            data = result.get("data") or []
            if isinstance(data, list):
                for t in data:
                    if (
                        isinstance(t, dict)
                        and t.get("hash", "").lower() == hash_str.lower()
                    ):
                        return t
        except Exception as e:
            err(str(e), "torbox.find_torrent_by_hash")
        return None

    def check_instant_availability(self, hashes: list[str]) -> set[str]:
        if not hashes:
            return set()
        cached: set[str] = set()

        result = self._post(
            "torrents/checkcached?format=list",
            json={
                "hashes": hashes,
                "format": "list",
            },
        )

        data: list[dict[str, Any]] = result.get("data") or []
        for h in data:
            hash = h.get("hash")
            if hash:
                cached.add(hash.lower())
        return cached

    def pick_video_file(
        self,
        files: list[dict[str, Any]],
        season: int | None = None,
        episode: int | None = None,
    ) -> dict[str, Any] | None:
        if not files:
            return None
        video = [
            f
            for f in files
            if any(f.get("name", "").lower().endswith(ext) for ext in _VIDEO_EXTS)
        ]
        candidates = video or files
        if season is not None and episode is not None:
            match = _match_episode_file(candidates, season, episode)
            if match:
                return match
        return max(candidates, key=lambda f: f.get("size", 0))


def _match_episode_file(
    files: list[dict[str, Any]], season: int, episode: int
) -> dict[str, Any] | None:
    patterns = [
        rf"[Ss]{season:02d}[Ee]{episode:02d}",
        rf"[Ss]{season}[Ee]{episode:02d}",
        rf"\b{season}[xX]{episode:02d}\b",
        rf"\b{season}[xX]{episode}\b",
    ]
    for f in files:
        name = f.get("name", "")
        short_name = f.get("short_name", "")
        for pattern in patterns:
            if not short_name == "":
                if re.search(pattern, short_name):
                    return f
            elif re.search(pattern, name):
                return f
    return None


def _extract_hash(magnet: str) -> str:
    """Extract the infohash from a magnet URI."""
    lower = magnet.lower()
    if "btih:" in lower:
        part = magnet[lower.index("btih:") + 5 :]
        return part.split("&")[0]
    return ""


TorBox = TorBoxAPI()
