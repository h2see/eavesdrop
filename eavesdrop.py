import argparse
import requests
import secrets
import json
from pathlib import Path
import spotipy
import time
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def main():
    # Parse command-line arguments.
    parser = argparse.ArgumentParser(description="Continuously sync the current Stats.fm stream to Spotify playback.")
    parser.add_argument(
        "statsfm_user",
        nargs="?",
        type=str,
        help="Stats.fm user ID whose stream you want to sync. If not provided, you will be prompted.",
    )
    parser.add_argument(
        "--device",
        type=str,
        help="Spotify device name or ID to use for playback. If not provided, the first available device will be used.",
    )
    parser.add_argument(
        "--sync_threshold",
        type=int,
        default=2000,
        help="Threshold in milliseconds for playback offset adjustments (default: 2000 ms).",
    )
    args = parser.parse_args()

    # Initialize Spotify client.
    try:
        sp = init_spotify()
    except Exception as e:
        logging.error("Error initializing Spotify client: %s", e)
        return

    # Prompt for the Stats.fm user ID if not provided.
    statsfm_user = args.statsfm_user if args.statsfm_user else input("Please enter the Stats.fm user ID: ")

    logging.info("Starting continuous sync for Stats.fm user: %s", statsfm_user)

    try:
        while True:
            try:
                sfm_user_stream = stats_fm_get_current_stream(statsfm_user)
                logging.debug("Fetched stream data: %s", sfm_user_stream)
            except Exception as e:
                logging.error("Error fetching stream for user %s: %s", statsfm_user, e)
                time.sleep(1)
                continue

            item = sfm_user_stream.get("item")
            if not item:
                logging.warning("StatsFM user %s is not currently playing anything!", statsfm_user)
                time.sleep(1)
                continue

            # Validate track details.
            track = item.get("track")
            if not track:
                logging.warning("Track information is missing in the StatsFM response.")
                time.sleep(1)
                continue

            duration = track.get("durationMs")
            if not duration:
                logging.warning("Duration is missing from track data.")
                time.sleep(1)
                continue

            external_ids = track.get("externalIds")
            if not external_ids:
                logging.warning("External IDs are missing from the track data.")
                time.sleep(1)
                continue

            spotify_ids = external_ids.get("spotify")
            if not spotify_ids or not isinstance(spotify_ids, list) or not spotify_ids:
                logging.warning("No Spotify track ID found in the StatsFM response.")
                time.sleep(1)
                continue

            current_spotify_id = spotify_ids[0]
            stats_progress_ms = item.get("progressMs")
            if stats_progress_ms is None or not isinstance(stats_progress_ms, int):
                logging.warning("Invalid or missing playback progress from StatsFM.")
                time.sleep(1)
                continue

            # Get available Spotify devices.
            devices_info = sp.devices()
            if devices_info is None:
                logging.warning("The devices() method returned None.")
                time.sleep(1)
                continue
            devices = devices_info.get("devices")
            if not devices:
                logging.warning("No active Spotify devices found. Please open Spotify on a device.")
                time.sleep(1)
                continue

            # Device selection: if --device is provided, try to match it by id or name.
            device_id = None
            if args.device:
                for device in devices:
                    if device.get("id") == args.device or device.get("name").lower() == args.device.lower():
                        device_id = device.get("id")
                        break
                if device_id is None:
                    logging.warning("No device matching '%s' found. Using the first available device.", args.device)
                    device_id = devices[0].get("id")
            else:
                device_id = devices[0].get("id")

            if not device_id:
                logging.warning("No valid device ID found in the Spotify devices list.")
                time.sleep(1)
                continue

            # Check current Spotify playback.
            sp_current = sp.current_playback()
            if sp_current is None or sp_current.get("item") is None:
                try:
                    sp.start_playback(
                        device_id=device_id,
                        uris=[f"spotify:track:{current_spotify_id}"],
                        position_ms=stats_progress_ms,
                    )
                    logging.info(
                        "No active playback. Started track %s at position %d ms.",
                        current_spotify_id,
                        stats_progress_ms,
                    )
                except Exception as e:
                    logging.error("Failed to start playback on Spotify: %s", e)
                time.sleep(1)
                continue

            sp_current_item = sp_current.get("item")
            sp_current_id = sp_current_item.get("id") if sp_current_item else None
            sp_progress_ms = sp_current.get("progress_ms", 0)

            # If the Spotify track does not match the Stats.fm track, switch tracks.
            if sp_current_id != current_spotify_id:
                try:
                    sp.start_playback(
                        device_id=device_id,
                        uris=[f"spotify:track:{current_spotify_id}"],
                        position_ms=stats_progress_ms,
                    )
                    logging.info("Switched to new track %s at position %d ms.", current_spotify_id, stats_progress_ms)
                except Exception as e:
                    logging.error("Failed to switch track on Spotify: %s", e)
            else:
                # If it's the same track, check if playback position is out of sync.
                if abs(sp_progress_ms - stats_progress_ms) > args.sync_threshold:
                    try:
                        sp.seek_track(stats_progress_ms, device_id=device_id)
                        logging.info("Adjusted playback position from %d to %d ms.", sp_progress_ms, stats_progress_ms)
                    except Exception as e:
                        logging.error("Error seeking track on Spotify: %s", e)
                else:
                    logging.info("Playback in sync. Track %s at %d ms.", current_spotify_id, sp_progress_ms)

            # Poll roughly every second.
            time.sleep(1)

    except KeyboardInterrupt:
        logging.info("Interrupted by user, exiting gracefully.")


def stats_fm_new_headers() -> dict:
    random_agent = secrets.token_urlsafe(16)
    return {"User-Agent": random_agent, "Accept": "application/json"}


def stats_fm_get_request(url: str, params: dict | None = None, headers: dict | None = None) -> dict:
    if headers is None:
        headers = stats_fm_new_headers()
    response = requests.get(url, params=params, headers=headers)
    if response.status_code != 200:
        raise Exception(f"BAD STATS FM RESPONSE: {response.reason}")
    return response.json()


def stats_fm_get_current_stream(user: str) -> dict:
    url = f"https://api.stats.fm/api/v1/users/{user}/streams/current"
    return stats_fm_get_request(url)


def init_spotify(creds="creds.json") -> spotipy.Spotify:
    creds_path = Path(creds)
    if not creds_path.is_file():
        raise FileNotFoundError(f"Credentials file '{creds}' not found.")
    with creds_path.open("r") as f:
        c = json.load(f)
    return spotipy.Spotify(
        auth_manager=spotipy.SpotifyOAuth(
            client_id=c["client_id"],
            client_secret=c["client_secret"],
            redirect_uri=c["redirect_uri"],
            scope=["user-read-playback-state", "user-modify-playback-state"],
        )
    )


if __name__ == "__main__":
    main()
