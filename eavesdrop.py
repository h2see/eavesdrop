import argparse
import requests
import secrets
import json
from pathlib import Path
import spotipy


def main():
    # Parse command-line arguments.
    parser = argparse.ArgumentParser(description="Sync the current Stats.fm stream to Spotify playback.")
    parser.add_argument(
        "statsfm_user",
        nargs="?",
        type=str,
        help="Stats.fm user ID whose stream you want to sync. If not provided, you will be prompted.",
    )
    args = parser.parse_args()

    # Initialize Spotify client.
    try:
        sp = init_spotify()
    except Exception as e:
        raise Exception(f"Error initializing Spotify client: {e}")

    # Prompt for the Stats.fm user ID if not provided on the command line.
    statsfm_user = args.statsfm_user if args.statsfm_user else input("Please enter the Stats.fm user ID: ")

    # Fetch current stream data from Stats.fm.
    try:
        sfm_user_stream = stats_fm_get_current_stream(statsfm_user)
    except Exception as e:
        raise Exception(f"Error fetching stream for user {statsfm_user}: {e}")

    item = sfm_user_stream.get("item")
    if item is None:
        raise Exception(f"StatsFM user {statsfm_user} is not currently playing anything!")

    # Validate track details.
    track = item.get("track")
    if not track:
        raise Exception("Track information is missing in the StatsFM response.")

    external_ids = track.get("externalIds")
    if not external_ids:
        raise Exception("External IDs are missing from the track data.")

    spotify_ids = external_ids.get("spotify")
    if not spotify_ids or not isinstance(spotify_ids, list) or not spotify_ids:
        raise Exception("No Spotify track ID found in the StatsFM response.")
    spotify_id = spotify_ids[0]

    # Validate playback progress.
    playback_offset = item.get("progressMs")
    if playback_offset is None or not isinstance(playback_offset, int):
        raise Exception("Invalid or missing playback progress from StatsFM.")

    # Get available Spotify devices.
    devices_info = sp.devices()
    if devices_info is None:
        raise Exception("The devices spotipy method returned None.")
    devices = devices_info.get("devices")
    if not devices:
        raise Exception("No active Spotify devices found. Please open Spotify on a device.")

    device_id = devices[0].get("id")
    if not device_id:
        raise Exception("No valid device ID found in the Spotify devices list.")

    # Check playback.
    sp_current = sp.current_playback()
    if sp_current is None:
        raise Exception("The current_playback spotipy method returned None.")
    sp_current_id = sp_current.get("item", {}).get("id")
    if sp_current_id is None:
        raise Exception("The spotipy user's current playback could not be determined.")

    # Start playback on the selected device.
    if sp_current_id != spotify_id:
        try:
            sp.start_playback(device_id=device_id, uris=[f"spotify:track:{spotify_id}"], position_ms=playback_offset)
        except Exception as e:
            raise Exception(f"Failed to start playback on Spotify: {e}")
    else:
        print("Did not update playback, already playing the same song.")


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
            open_browser=False,
        )
    )


if __name__ == "__main__":
    main()
