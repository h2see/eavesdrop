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
    parser = argparse.ArgumentParser(description="Sync the current Stats.fm stream to Spotify playback.")
    parser.add_argument(
        "statsfm_user",
        nargs="?",
        type=str,
        help="Stats.fm user ID whose stream you want to sync. If not provided, you will be prompted.",
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Continuously sync playback until interrupted.",
    )
    parser.add_argument(
        "--device",
        type=str,
        help="Spotify device name or ID to use for playback. If not provided, the first available device will be used.",
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
    loop_mode = args.loop

    last_spotify_id = None

    try:
        while True:
            try:
                sfm_user_stream = stats_fm_get_current_stream(statsfm_user)
                logging.info("Fetched stream data: %s", sfm_user_stream)
            except Exception as e:
                logging.error("Error fetching stream for user %s: %s", statsfm_user, e)
                if loop_mode:
                    time.sleep(5)
                    continue
                else:
                    return

            item = sfm_user_stream.get("item")
            if item is None:
                logging.warning("StatsFM user %s is not currently playing anything!", statsfm_user)
                if loop_mode:
                    time.sleep(5)
                    continue
                else:
                    return

            # Validate track details.
            track = item.get("track")
            if not track:
                logging.warning("Track information is missing in the StatsFM response.")
                if loop_mode:
                    time.sleep(5)
                    continue
                else:
                    return

            duration = track.get("durationMs")
            if not duration:
                logging.warning("Duration is missing from track data.")
                if loop_mode:
                    time.sleep(5)
                    continue
                else:
                    return

            external_ids = track.get("externalIds")
            if not external_ids:
                logging.warning("External IDs are missing from the track data.")
                if loop_mode:
                    time.sleep(5)
                    continue
                else:
                    return

            spotify_ids = external_ids.get("spotify")
            if not spotify_ids or not isinstance(spotify_ids, list) or not spotify_ids:
                logging.warning("No Spotify track ID found in the StatsFM response.")
                if loop_mode:
                    time.sleep(5)
                    continue
                else:
                    return

            current_spotify_id = spotify_ids[0]
            playback_offset = item.get("progressMs")
            if playback_offset is None or not isinstance(playback_offset, int):
                logging.warning("Invalid or missing playback progress from StatsFM.")
                if loop_mode:
                    time.sleep(5)
                    continue
                else:
                    return

            # In loop mode, if the current Spotify track ID hasn't changed from the last iteration,
            # poll the Stats.fm API every 0.75 seconds up to 5 times to check for an update.
            if loop_mode and last_spotify_id is not None and current_spotify_id == last_spotify_id:
                updated = False
                for attempt in range(5):
                    time.sleep(0.75)
                    try:
                        sfm_user_stream = stats_fm_get_current_stream(statsfm_user)
                    except Exception as e:
                        logging.error("Error fetching stream during update check: %s", e)
                        break
                    item_retry = sfm_user_stream.get("item")
                    if item_retry is None:
                        break
                    track_retry = item_retry.get("track")
                    if not track_retry:
                        break
                    external_ids_retry = track_retry.get("externalIds")
                    if not external_ids_retry:
                        break
                    spotify_ids_retry = external_ids_retry.get("spotify")
                    if not spotify_ids_retry or not isinstance(spotify_ids_retry, list) or not spotify_ids_retry:
                        break
                    new_spotify_id = spotify_ids_retry[0]
                    if new_spotify_id != last_spotify_id:
                        current_spotify_id = new_spotify_id
                        playback_offset = item_retry.get("progressMs")
                        duration = track_retry.get("durationMs")
                        updated = True
                        logging.info("StatsFM API updated to new track: %s", new_spotify_id)
                        break
                if not updated:
                    logging.info("No update in StatsFM API after retries; restarting same track from beginning.")
                    playback_offset = 0  # Restart the same song from beginning

            remaining_ms = duration - playback_offset
            start = time.perf_counter()
            end = start + (remaining_ms / 1000)

            # Get available Spotify devices.
            devices_info = sp.devices()
            if devices_info is None:
                logging.warning("The devices spotipy method returned None.")
                if loop_mode:
                    time.sleep(5)
                    continue
                else:
                    return
            devices = devices_info.get("devices")
            if not devices:
                logging.warning("No active Spotify devices found. Please open Spotify on a device.")
                if loop_mode:
                    time.sleep(5)
                    continue
                else:
                    return

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
                if loop_mode:
                    time.sleep(5)
                    continue
                else:
                    return

            # Check current Spotify playback.
            sp_current = sp.current_playback()
            if sp_current is None:
                logging.warning("The current_playback spotipy method returned None.")
                if loop_mode:
                    time.sleep(5)
                    continue
                else:
                    return

            sp_current_id = sp_current.get("item", {}).get("id")
            if sp_current_id is None:
                logging.warning("The spotipy user's current playback could not be determined.")
                if loop_mode:
                    time.sleep(5)
                    continue
                else:
                    return

            # Start playback on the selected device if needed.
            if sp_current_id != current_spotify_id:
                try:
                    sp.start_playback(
                        device_id=device_id,
                        uris=[f"spotify:track:{current_spotify_id}"],
                        position_ms=playback_offset,
                    )
                    logging.info(
                        "Started playback of track %s at position %d ms.", current_spotify_id, playback_offset
                    )
                except Exception as e:
                    logging.error("Failed to start playback on Spotify: %s", e)
                    if loop_mode:
                        time.sleep(5)
                        continue
                    else:
                        return
            else:
                logging.info("Did not update playback, already playing the same song.")

            # Wait until the track is expected to finish.
            now = time.perf_counter()
            while now < end:
                time.sleep(0.1)
                now = time.perf_counter()

            logging.info("Track ended or sync interval complete.")
            last_spotify_id = current_spotify_id

            if not loop_mode:
                break

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
